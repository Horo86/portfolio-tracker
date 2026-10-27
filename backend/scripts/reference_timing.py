"""Server times of the screens at the reference size, against the 2-second target (D-154).

Run it on the reference data of `dev_data.py --reference`, logged in as its first user. In
process (the default) it opens the data directory given in `PT_DATA_DIR`, whose name must
start with `.data`, with market data off; it never creates a database. With `--url` it times
a running server instead, on this computer only (127.0.0.1 or localhost), such as the image
started on the reference data. It reads only, except one transaction that it creates and
deletes again to time a change. Never run in CI; the numbers depend on the machine, so keep
them out of the repository.

    PT_DATA_DIR=.data-ref uv run python scripts/reference_timing.py
    uv run python scripts/reference_timing.py --url http://127.0.0.1:8000 --json /tmp/t.json

Cold is the dashboard of all accounts as the first request after the server starts (in process:
after the app starts); every other figure is the median, 90th percentile and maximum of
`--runs` runs, after two that are left out.
"""

import argparse
import json
import os
import statistics
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
from fastapi.testclient import TestClient

from portfolio_tracker.config import Settings
from portfolio_tracker.main import create_app

CSRF = {"X-Requested-With": "portfolio-tracker"}
USER = ("ref01", "dev-ref-password")
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
WARM_UP = 2
PAGE = 50  # the list's page size (D-141)
# D-154 (6): warm median limits in seconds; the dashboard's cold limit is 2 s.
DASHBOARD, CHANGE, SCREEN, COLD = 1.5, 2.0, 1.0, 2.0


class Refused(Exception):
    pass


def local_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.hostname not in LOCAL_HOSTS:
        raise Refused(f"{url} is not this computer (127.0.0.1 or localhost)")
    return url.rstrip("/")


def client_for(args: argparse.Namespace, settings: Settings | None) -> AbstractContextManager:
    if args.url:
        return httpx2.Client(base_url=local_url(args.url), timeout=60)
    if settings is None and not os.environ.get("PT_DATA_DIR"):
        raise Refused("set PT_DATA_DIR to the reference data directory, e.g. .data-ref")
    settings = (settings or Settings()).model_copy(update={"market_data": False})
    if not settings.data_dir.name.startswith(".data"):
        raise Refused(f"{settings.data_dir} is not a development data directory")
    if not settings.db_path.exists():
        raise Refused(f"no database in {settings.data_dir}: create it with dev_data.py first")
    # http + Secure cookies would be dropped by the client: same as the tests, use https.
    return TestClient(create_app(settings), base_url="https://localhost")


class Timer:
    def __init__(self, client, runs: int) -> None:
        self.client, self.runs, self.results = client, runs, []

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        response = self.client.request(method, f"/api/v1{path}", json=body, headers=CSRF)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text}")
        return response.json() if response.content else {}

    def together(self, paths: list[str]) -> Callable[[], None]:
        """The paths sent at once, as the browser sends a page's requests."""

        def run() -> None:
            with ThreadPoolExecutor(len(paths)) as pool:
                for future in [pool.submit(self.call, "GET", p) for p in paths]:
                    future.result()

        return run

    def once(self, label: str, run: Callable[[], None], limit: float) -> None:
        started = time.perf_counter()
        run()
        self.record(label, [time.perf_counter() - started], limit)

    def repeat(self, label: str, run: Callable[[], object], limit: float | None) -> None:
        times = []
        for n in range(WARM_UP + self.runs):
            started = time.perf_counter()
            run()
            if n >= WARM_UP:
                times.append(time.perf_counter() - started)
        self.record(label, times, limit)

    def record(self, label: str, times: list[float], limit: float | None) -> None:
        ordered = sorted(times)
        row = {
            "label": label,
            "median": statistics.median(ordered),
            "p90": ordered[min(len(ordered) - 1, round(0.9 * (len(ordered) - 1)))],
            "max": ordered[-1],
            "limit": limit,
        }
        if limit is not None:
            row["verdict"] = "within" if row["median"] <= limit else "over"
        self.results.append(row)
        verdict = f"{row['verdict']} {limit:.1f} s" if limit is not None else "(no limit)"
        print(
            f"{label:<58} median {row['median']:7.3f}  p90 {row['p90']:7.3f}  "
            f"max {row['max']:7.3f}  {verdict}",
            flush=True,
        )


def dashboard(prefix: str) -> list[str]:
    return [f"{prefix}/summary", f"{prefix}/positions?include_closed=true", f"{prefix}/breakdown"]


def measure(timer: Timer) -> None:
    portfolio = dashboard("/portfolio")
    timer.once("dashboard, all accounts, cold", timer.together(portfolio), COLD)
    timer.repeat("dashboard, all accounts (3 requests together)", timer.together(portfolio),
                 DASHBOARD)  # fmt: skip
    for path in portfolio:
        timer.repeat(f"  {path}", lambda p=path: timer.call("GET", p), None)

    accounts = timer.call("GET", "/accounts")
    largest = max(accounts, key=lambda a: a["transaction_count"])
    timer.repeat("dashboard, largest account (3 requests together)",
                 timer.together(dashboard(f"/accounts/{largest['id']}")), DASHBOARD)  # fmt: skip

    holdings = timer.call("GET", f"/accounts/{largest['id']}/positions?include_closed=true")
    asset = next(h for h in holdings if h["quote_currency"] == largest["currency"])
    buy = {
        "account_id": largest["id"],
        "type": "BUY",
        "asset_id": asset["asset_id"],
        "trade_date": datetime.now(UTC).date().isoformat(),
        "quantity": "1",
        "unit_price": "100",
        "gross_amount": "100.00",
        "fees": "1.00",
        "total_amount": "101.00",
    }
    draft = {"transaction": buy}
    timer.repeat("form preview (POST /transactions/preview)",
                 lambda: timer.call("POST", "/transactions/preview", draft), SCREEN)  # fmt: skip
    created: list[str] = []
    refresh = timer.together(portfolio)

    def change() -> None:
        created.append(timer.call("POST", "/transactions", buy)["transactions"][0]["id"])
        refresh()

    try:
        timer.repeat("after a change (create, then the dashboard's 3 requests)", change, CHANGE)
    finally:
        deletes = []
        for tx_id in created:
            started = time.perf_counter()
            timer.call("DELETE", f"/transactions/{tx_id}")
            deletes.append(time.perf_counter() - started)
    timer.record("delete (DELETE /transactions/{id}, one each)", deletes[WARM_UP:] or deletes,
                 SCREEN)  # fmt: skip

    timer.repeat("holdings list (/portfolio/positions?include_closed=true)",
                 lambda: timer.call("GET", portfolio[1]), SCREEN)  # fmt: skip
    detail = [portfolio[1], f"/transactions?asset_id={asset['asset_id']}&limit={PAGE}&offset=0"]
    timer.repeat("holding detail (positions and its first transactions)", timer.together(detail),
                 SCREEN)  # fmt: skip
    first_page = f"/transactions?limit={PAGE}&offset=0"
    timer.repeat("transactions, first page", lambda: timer.call("GET", first_page), SCREEN)
    timer.repeat("accounts (GET /accounts)", lambda: timer.call("GET", "/accounts"), SCREEN)


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", help="time a running server on this computer instead")
    parser.add_argument("--runs", type=int, default=20, help="timed runs of each (default 20)")
    parser.add_argument("--json", type=Path, help="also write the numbers to this file")
    args = parser.parse_args(argv)
    try:
        context = client_for(args, settings)
    except Refused as exc:
        print(f"refused: {exc}")
        return 2
    with context as client:
        timer = Timer(client, args.runs)
        timer.call("POST", "/auth/login", {"username": USER[0], "password": USER[1]})
        measure(timer)
    if args.json:
        numbers = {"user": USER[0], "runs": args.runs, "url": args.url, "results": timer.results}
        args.json.write_text(json.dumps(numbers, indent=2))
    over = [r["label"] for r in timer.results if r.get("verdict") == "over"]
    print("all within the D-154 limits" if not over else f"over the limits: {', '.join(over)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
