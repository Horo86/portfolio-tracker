"""Test-only launcher for the end-to-end tests with market data on (M5 planning, Q-9): the app
with every provider answered by invented fakes. It lives in `backend/tests/`, which
`.dockerignore` keeps out of the image (CI checks it), and is not collected by pytest.

Started by `frontend/e2e/serve.sh market PORT` on a fresh folder whose name starts with
`.data` (it refuses any other): `uv run python tests/e2e_market_server.py`, with the `PT_*`
variables of the app; it always listens on 127.0.0.1 and serves cookies over plain http.

What the fakes answer (everything invented, D-151; the fake transport is the only HTTP path,
so no call leaves the machine):
- Yahoo search: `WORLD_ISIN` → three listings, two in EUR on two exchanges and one London
  line in pence; `STOCK_ISIN` → one EUR listing; another ISIN → nothing; a text → the
  `TEXT_SYMBOL` listing, without ISIN (D-128).
- Yahoo charts: closes on the `CLOSE_DAYS` weekdays before today, so linking ends `OK` with
  the real clock (a listing needs a recent close, D-136).
- OpenFIGI knows nothing; Frankfurter has no rates.

The gate: the background job's chart calls for a symbol in `GATED` wait until a file
`gate-<symbol>` exists in the data folder (at most `GATE_SECONDS`, then a 503), so an asset
with that ISIN stays `PENDING` until the test writes the file. A search from a request never
waits. A run is logged for today before the start, so no catch-up runs beside the tests.
"""

import json
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI
from helpers import fake_isin
from marketdata.fakes import FakeRequest, FakeTransport, InstantTime, json_response

from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine, db_transaction
from portfolio_tracker.main import create_app
from portfolio_tracker.marketdata.http import HttpResponse
from portfolio_tracker.marketdata.registry import default_registry
from portfolio_tracker.migrations_runner import run_migrations
from portfolio_tracker.repositories import job_runs
from portfolio_tracker.textformats import valid_isin

WORLD_ISIN = fake_isin(8701)
STOCK_ISIN = fake_isin(8702)
TEXT_SYMBOL = "ZTXT.MI"
GATED = frozenset({"ZWLD.MI"})
GATE_SECONDS = 60.0
CLOSE_DAYS = 3
JOB_THREAD = "market-data"  # the scheduler's thread (services/scheduler.py)


# Yahoo exchange code → (the name Yahoo shows, the exchange's time zone).
EXCHANGES = {
    "MIL": ("Milan", "Europe/Rome"),
    "GER": ("XETRA", "Europe/Berlin"),
    "LSE": ("London", "Europe/London"),
}


@dataclass(frozen=True)
class Line:
    """One invented listing: its search entry and its chart."""

    symbol: str
    name: str
    kind: str  # Yahoo's quoteType
    exchange: str  # a key of EXCHANGES
    currency: str  # as Yahoo writes it: GBp for pence
    closes: tuple[float, ...]  # one per day of recent_weekdays

    def quote(self) -> dict:
        return {
            "exchange": self.exchange,
            "shortname": self.name[:24],
            "quoteType": self.kind,
            "symbol": self.symbol,
            "index": "quotes",
            "score": 20000.0,
            "typeDisp": self.kind.title(),
            "longname": self.name,
            "exchDisp": EXCHANGES[self.exchange][0],
            "isYahooFinance": True,
        }

    def chart(self, today: date) -> dict:
        display, zone = EXCHANGES[self.exchange]
        stamps = [
            int(datetime(d.year, d.month, d.day, 9, tzinfo=ZoneInfo(zone)).timestamp())
            for d in recent_weekdays(today)
        ]
        meta = {
            "currency": self.currency,
            "symbol": self.symbol,
            "exchangeName": self.exchange,
            "fullExchangeName": display,
            "instrumentType": self.kind,
            "regularMarketTime": stamps[-1],
            "exchangeTimezoneName": zone,
            "priceHint": 2,
            "dataGranularity": "1d",
        }
        result = {
            "meta": meta,
            "timestamp": stamps,
            "indicators": {"quote": [{"close": self.closes}]},
        }
        return {"chart": {"result": [result], "error": None}}


WORLD = "Example Market World UCITS ETF"
STOCK = "Example Market Industrial SpA"
SEARCH = {
    WORLD_ISIN: (
        Line("ZWLD.MI", WORLD, "ETF", "MIL", "EUR", (101.25, 101.5, 101.75)),
        Line("ZWLD.DE", WORLD, "ETF", "GER", "EUR", (101.0, 101.25, 101.5)),
        Line("ZWLD.L", WORLD, "ETF", "LSE", "GBp", (8650.0, 8662.5, 8675.0)),
    ),
    STOCK_ISIN: (Line("ZSTK.MI", STOCK, "EQUITY", "MIL", "EUR", (24.25, 24.5, 24.75)),),
}
TEXT = Line(
    TEXT_SYMBOL, "Example Market Text Holding", "EQUITY", "MIL", "EUR", (12.25, 12.5, 12.75)
)
LINES = {line.symbol: line for lines in SEARCH.values() for line in lines} | {TEXT.symbol: TEXT}


def recent_weekdays(today: date) -> list[date]:
    """The `CLOSE_DAYS` weekdays before `today`, oldest first."""
    days: list[date] = []
    day = today
    while len(days) < CLOSE_DAYS:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            days.insert(0, day)
    return days


class FakeMarket:
    """The fake providers' answers, for a transport of the app."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.waiting = threading.Event()  # set while a job's call waits for a gate

    def __call__(self, request: FakeRequest) -> HttpResponse:
        if request.url.endswith("/v1/finance/search"):
            q = request.params.get("q", "").strip().upper()
            lines = SEARCH.get(q, ()) if valid_isin(q) else (TEXT,)
            quotes = [line.quote() for line in lines]
            return json_response({"explains": [], "count": len(quotes), "quotes": quotes,
                                  "news": [], "nav": [], "lists": []})  # fmt: skip
        if "/v8/finance/chart/" in request.url:
            symbol = request.url.rsplit("/", 1)[1]
            line = LINES.get(symbol)
            if line is None:
                error = {"code": "Not Found", "description": "No data found"}
                return json_response({"chart": {"result": None, "error": error}}, 404)
            if not self._through_gate(symbol):
                return json_response({}, 503)
            return json_response(line.chart(datetime.now(UTC).date()))
        if "openfigi" in request.url:
            jobs = json.loads(request.body or b"[]")
            return json_response([{"warning": "No identifier found."} for _ in jobs])
        if "frankfurter" in request.url:
            return json_response([])
        return json_response({}, 404)

    def _through_gate(self, symbol: str) -> bool:
        """False when the job waited `GATE_SECONDS` for the gate of `symbol` in vain."""
        if symbol not in GATED or threading.current_thread().name != JOB_THREAD:
            return True
        gate = self.data_dir / f"gate-{symbol}"
        deadline = time.monotonic() + GATE_SECONDS
        self.waiting.set()
        try:
            while not gate.exists():
                if time.monotonic() > deadline:
                    return False
                time.sleep(0.1)
            return True
        finally:
            self.waiting.clear()


class Refused(Exception):
    pass


def settings_from_environment() -> Settings:
    """The app's settings from `PT_*`, with market data on and cookies over http; refuses a
    data folder whose name does not start with `.data` (never a real installation)."""
    settings = Settings()
    if not settings.data_dir.resolve().name.startswith(".data"):
        raise Refused("PT_DATA_DIR must be a folder whose name starts with .data")
    return settings.model_copy(update={"market_data": True, "cookie_secure": False})


def build_app(settings: Settings, market: FakeMarket) -> FastAPI:
    """The app on the fakes, with throttles that never sleep and a run logged today."""
    engine = create_db_engine(settings.db_path)
    try:
        run_migrations(engine, settings.db_path, settings.pre_migration_backup_dir)
        with db_transaction(engine, write=True) as session:
            now = datetime.now(UTC)
            run = job_runs.start_run(session, "DAILY", now)
            job_runs.finish_run(session, run.id, result="OK", at=now, counts={}, errors={})
    finally:
        engine.dispose()
    instant = InstantTime()
    return create_app(
        settings,
        market_transport=FakeTransport(market),
        market_registry=default_registry(instant.clock, instant.sleep),
    )


def main() -> int:
    try:
        settings = settings_from_environment()
    except Refused as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    app = build_app(settings, FakeMarket(settings.data_dir))
    uvicorn.run(app, host="127.0.0.1", port=settings.port, log_config=None, proxy_headers=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
