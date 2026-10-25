"""Opt-in live check of market data on public securities (D-151).

Runs the app's whole market data flow once against the real services (Yahoo, OpenFIGI,
Frankfurter) on a throwaway data folder, and prints a report per ISIN: what the search
offers, the class, the listing the linking job chose and whether it is the security's home
listing, the price status, the last close, and the FX rates. Never in CI and never on real
data: the securities are the public ones of `ENTRIES` (D-151, hard rule 1); `--extra
ISIN:CURRENCY` adds up to `MAX_EXTRAS` more for one run, reported "not judged" and kept
nowhere but in the folder, which is deleted at the end.

    PT_LIVE_CHECK=1 PT_DATA_DIR=/tmp/.data-live-check uv run python scripts/live_check.py

Guards, before anything is created: `PT_LIVE_CHECK=1`; `PT_DATA_DIR` given, its name starting
with `.data`, and the folder not existing yet (the check creates it, and deletes it at the end
unless `--keep`). It is not part of the image.

One pass, through the app's own code in process: with market data off, an administrator with a
random password (never printed), one account per quote currency, the assets and one invented
purchase each (through the batch routes, D-147); then the app with market data on, whose start
queues exactly one catch-up run (no run was ever logged): linking, classes, listings, closes
and FX, with the production engines and throttles. Then one search per ISIN and one text
search, `SEARCH_SPACING` seconds apart. The only addition is `CountingTransport` around the
real transport: it counts the calls, and after the first 429 or 503 of a service, or past
`call_cap`, it refuses every further call locally and the check stops (exit code 3).

Load: at most `max_calls` = 17 calls per ISIN (OpenFIGI 1; Yahoo: classify 1, search 1 and 6
chart checks, closes 1; the search of the page 1 and 6), 7 for the text search and 1 per
currency other than EUR (Frankfurter). The app's throttles keep at least 1 s between two Yahoo
calls and 2.5 s between two OpenFIGI calls.
"""

import argparse
import os
import re
import secrets
import shutil
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine, db_transaction
from portfolio_tracker.db.models import (
    Asset,
    FxRate,
    Instrument,
    InstrumentEngineCheck,
    InstrumentListing,
    JobRun,
)
from portfolio_tracker.main import create_app
from portfolio_tracker.marketdata.base import ErrorCode, ProviderError
from portfolio_tracker.marketdata.http import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT,
    HttpResponse,
    Transport,
    UrllibTransport,
)
from portfolio_tracker.marketdata.registry import Registry
from portfolio_tracker.textformats import valid_isin

CSRF = {"X-Requested-With": "portfolio-tracker"}
USERNAME = "live-check"
TEXT_QUERY = "apple"
MAX_EXTRAS = 3
SEARCH_SPACING = 5.0  # seconds between two searches of the page
POLL_SECONDS = 2.0  # the app's own status route, on localhost
RUN_TIMEOUT = 600.0
PER_ISIN = 17
PER_TEXT = 7
STOP_STATUSES = frozenset({429, 503})


@dataclass(frozen=True)
class Entry:
    isin: str
    name: str
    asset_type: str  # ETF | STOCK | BOND
    currency: str
    home: str | None  # Yahoo's exchange code of the home listing, when the sources agree
    note: str  # the public source of `home`, or why there is none
    extra: bool = False  # given with --extra: never judged

    @property
    def unit(self) -> str:
        return "PERCENT_OF_NOMINAL" if self.asset_type == "BOND" else "CURRENCY"


NO_PRIMARY = "London in USD and several EU exchanges; no public source names a primary listing"
ENTRIES = (
    Entry("IE00B4L5Y983", "iShares Core MSCI World UCITS ETF USD (Acc)", "ETF", "EUR", None,
          NO_PRIMARY),
    Entry("IE00BK5BQT80", "Vanguard FTSE All-World UCITS ETF (USD) Accumulating", "ETF", "EUR",
          None, NO_PRIMARY),
    Entry("US0378331005", "Apple Inc.", "STOCK", "USD", "NMS", "Nasdaq Global Select, AAPL"),
    Entry("IT0005240350", "BTP 2.45% 1 Sep 2033", "BOND", "EUR", None,
          "Borsa Italiana MOT; no engine for bonds, so NOT_AVAILABLE is expected"),
    Entry("IE0005042456", "iShares Core FTSE 100 UCITS ETF (Dist)", "ETF", "GBP", "LSE",
          "iShares fact sheet, Bloomberg ISF LN, in pence"),
)  # fmt: skip


def extra_entry(n: int, isin: str, currency: str) -> Entry:
    return Entry(isin, f"Extra {n}", "ETF", currency, None, "--extra", extra=True)


def max_calls(entries: Sequence[Entry]) -> int:
    return PER_ISIN * len(entries) + PER_TEXT + len({e.currency for e in entries} - {"EUR"})


def call_cap(entries: Sequence[Entry]) -> int:
    """`max_calls` with a margin: reached only if something loops."""
    most = max_calls(entries)
    return most + max(5, most // 10)


def new_password() -> str:
    return secrets.token_urlsafe(24)


class CountingTransport:
    """The real transport, counted: calls and the shortest gap per host. After the first 429
    or 503 of a service, or past `cap` calls, every further call is refused here, with no
    network (`stopped` says why)."""

    def __init__(self, inner: Transport, cap: int, clock: Callable[[], float] = time.monotonic):
        self._inner, self._cap, self._clock = inner, cap, clock
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self.calls: Counter[str] = Counter()
        self.gaps: dict[str, float] = {}
        self.stopped: str | None = None

    def fetch(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        headers: Mapping[str, str] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        body: bytes | None = None,
    ) -> HttpResponse:
        host = urlsplit(url).hostname or "?"
        with self._lock:
            if self.stopped is None and self.calls.total() >= self._cap:
                self.stopped = f"the cap of {self._cap} calls"
            if self.stopped is not None:
                raise ProviderError(ErrorCode.RATE_LIMITED, "live check stopped")
            now = self._clock()
            if host in self._last:
                gap = now - self._last[host]
                self.gaps[host] = min(gap, self.gaps.get(host, gap))
            self._last[host] = now
            self.calls[host] += 1
        response = self._inner.fetch(
            url, params, timeout=timeout, headers=headers, max_bytes=max_bytes, body=body
        )
        if response.status in STOP_STATUSES:
            with self._lock:
                self.stopped = self.stopped or f"{response.status} from {host}"
        return response


@dataclass
class Row:
    entry: Entry
    asset_id: str
    offered: list[dict] | str | None = None  # the search's results, or its error
    price_class: str | None = None
    class_source: str | None = None
    checks: list[str] = field(default_factory=list)
    listing: InstrumentListing | None = None
    position: dict = field(default_factory=dict)


@dataclass
class Outcome:
    started: datetime
    folder: Path
    rows: list[Row]
    transport: CountingTransport
    most: int
    text_query: str
    accounts: dict[str, str] = field(default_factory=dict)  # currency → account id
    stopped: str | None = None
    text: list[dict] | str | None = None
    runs: list[JobRun] = field(default_factory=list)
    fx: list[tuple[str, date, date, int, str]] = field(default_factory=list)
    kept: bool = False


def _call(client: TestClient, method: str, path: str, body: dict | None = None) -> dict:
    response = client.request(method, path, json=body, headers=CSRF)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"{method} {path}: {response.status_code} {response.text}")
    return response.json()


def _prepare(
    settings: Settings, entries: Sequence[Entry], password: str
) -> tuple[list[str], dict[str, str]]:
    """Market data off: the administrator, one account per currency, the assets and one
    purchase each. Returns the asset ids in the order of `entries`, and the account id of
    each currency."""
    day = (datetime.now(UTC).date() - timedelta(days=30)).isoformat()
    with TestClient(create_app(settings), base_url="https://localhost") as client:
        _call(client, "POST", "/api/v1/setup/admin",
              {"username": USERNAME, "password": password, "language": "en"})  # fmt: skip
        accounts = {
            currency: _call(client, "POST", "/api/v1/accounts",
                            {"name": f"Live check {currency}", "currency": currency})["id"]
            for currency in sorted({e.currency for e in entries})
        }  # fmt: skip
        ids, rows = [], []
        for e in entries:
            asset = _call(client, "POST", "/api/v1/assets", {
                "name": e.name, "asset_type": e.asset_type, "isin": e.isin,
                "quote_currency": e.currency, "quote_unit": e.unit,
            })  # fmt: skip
            ids.append(asset["id"])
            quantity = "100" if e.asset_type == "BOND" else "1"  # a bond: nominal at 100 %
            rows.append({"account_id": accounts[e.currency], "type": "BUY", "trade_date": day,
                         "asset_id": asset["id"], "quantity": quantity, "unit_price": "100",
                         "affects_cash": False})  # fmt: skip
        # Each row in an account of its own currency: the preview completes both amounts.
        done = _call(client, "POST", "/api/v1/transactions/batch/preview", {"transactions": rows})
        _call(client, "POST", "/api/v1/transactions/batch", {"transactions": done["transactions"]})
    return ids, accounts


def _wait_for_the_run(client: TestClient, outcome: Outcome, poll: float, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while outcome.transport.stopped is None:
        status = _call(client, "GET", "/api/v1/admin/market-data/status")
        last = status["last_run"]
        if last and last["finished_at"] and not status["running"]:
            return
        if time.monotonic() > deadline:
            outcome.stopped = f"the run did not finish in {timeout:.0f} s"
            return
        time.sleep(poll)


def _search(client: TestClient, q: str) -> tuple[list[dict] | str, str | None]:
    """The page's search: its results, or its error code and whether that stops the check."""
    response = client.post("/api/v1/assets/search", headers=CSRF, json={"q": q})
    if response.status_code == 200:
        return response.json(), None
    body = response.json()
    error = f"{response.status_code} {body.get('code')}: {body.get('detail')}"
    return error, (f"search: {error}" if response.status_code in STOP_STATUSES else None)


def _check(settings: Settings, outcome: Outcome, password: str, text: str, spacing: float,
           poll: float, timeout: float, registry: Registry | None) -> None:  # fmt: skip
    """Market data on: the one catch-up run, then the searches and the positions."""
    app = create_app(settings, market_transport=outcome.transport, market_registry=registry)
    with TestClient(app, base_url="https://localhost") as client:
        _call(client, "POST", "/api/v1/auth/login", {"username": USERNAME, "password": password})
        _wait_for_the_run(client, outcome, poll, timeout)
        for row in outcome.rows:
            if outcome.stopped or outcome.transport.stopped:
                break
            time.sleep(spacing)
            row.offered, outcome.stopped = _search(client, row.entry.isin)
        if not (outcome.stopped or outcome.transport.stopped):
            time.sleep(spacing)
            outcome.text, outcome.stopped = _search(client, text)
        # Each account's own positions, in its currency: no exchange rate needed.
        by_asset = {
            p["asset_id"]: p
            for account_id in outcome.accounts.values()
            for p in _call(client, "GET", f"/api/v1/accounts/{account_id}/positions")
        }
    for row in outcome.rows:
        row.position = by_asset.get(row.asset_id, {})
    outcome.stopped = outcome.transport.stopped or outcome.stopped


def _read(settings: Settings, outcome: Outcome) -> None:
    """The class, checks and active listing of each asset, the rates and the runs: read-only,
    after the app has stopped."""
    engine = create_db_engine(settings.db_path)
    try:
        with db_transaction(engine, write=False) as session:
            for row in outcome.rows:
                asset = session.get(Asset, UUID(row.asset_id))
                if asset is None or asset.instrument_id is None:
                    continue
                instrument = session.get(Instrument, asset.instrument_id)
                if instrument is None:
                    continue
                row.price_class, row.class_source = instrument.price_class, instrument.class_source
                for check in session.scalars(
                    select(InstrumentEngineCheck)
                    .where(InstrumentEngineCheck.instrument_id == instrument.id)
                    .order_by(InstrumentEngineCheck.provider)
                ):
                    found = f" {check.symbol}" if check.symbol else ""
                    error = f" ({check.last_error_code})" if check.last_error_code else ""
                    row.checks.append(f"{check.provider} {check.status}{found}{error}")
                row.listing = session.scalars(
                    select(InstrumentListing).where(
                        InstrumentListing.instrument_id == instrument.id,
                        InstrumentListing.is_active,
                    )
                ).one_or_none()
                if row.listing is not None:
                    session.expunge(row.listing)
            for currency, first, last, days in session.execute(
                select(FxRate.currency, func.min(FxRate.day), func.max(FxRate.day), func.count())
                .group_by(FxRate.currency)
                .order_by(FxRate.currency)
            ):
                rate = session.scalar(
                    select(FxRate.rate).where(FxRate.currency == currency, FxRate.day == last)
                )
                outcome.fx.append((currency, first, last, days, str(rate)))
            outcome.runs = list(session.scalars(select(JobRun).order_by(JobRun.started_at)))
            session.expunge_all()
    finally:
        engine.dispose()


def run(settings: Settings, entries: Sequence[Entry], inner: Transport, *,
        extras: Sequence[Entry] = (), text: str = TEXT_QUERY, keep: bool = False,
        spacing: float = SEARCH_SPACING, poll: float = POLL_SECONDS,
        timeout: float = RUN_TIMEOUT, out: Callable[[str], object] = print,
        registry: Registry | None = None) -> int:  # fmt: skip
    """The whole check on the new folder `settings.data_dir` (created here). Returns 0, or 3
    when it stopped early. `registry` is for the tests only (throttles that do not sleep);
    the check itself runs the engines the app ships with."""
    everything = [*entries, *extras]
    outcome = Outcome(
        datetime.now(UTC),
        settings.data_dir,
        [],
        CountingTransport(inner, call_cap(everything)),
        max_calls(everything),
        text,
        kept=keep,
    )
    password = new_password()
    settings.data_dir.mkdir(parents=True)
    try:
        offline = settings.model_copy(update={"market_data": False})
        ids, outcome.accounts = _prepare(offline, everything, password)
        outcome.rows = [Row(e, i) for e, i in zip(everything, ids, strict=True)]
        online = settings.model_copy(update={"market_data": True})
        _check(online, outcome, password, text, spacing, poll, timeout, registry)
        _read(settings, outcome)
    finally:
        if not keep:
            shutil.rmtree(settings.data_dir)
    for line in format_report(outcome):
        out(line)
    return 3 if outcome.stopped else 0


# --- the report ---------------------------------------------------------------------------


def _verdict(entry: Entry, listing: InstrumentListing | None) -> str:
    if entry.extra:
        return "not judged (--extra)"
    if listing is None:
        return "no listing"
    if entry.home is None:
        return f"not judged ({entry.note})"
    if listing.exchange_code == entry.home:
        return f"home ({entry.note})"
    return f"secondary (home: {entry.home}, {entry.note})"


def _offered(results: list[dict]) -> str:
    def one(r: dict) -> str:
        pence = " in pence" if r["quote_unit"] == "MINOR_CURRENCY_UNIT" else ""
        return f"{r['symbol']} {r['exchange'] or '?'} {r['currency']}{pence}"

    if not results:
        return "offers nothing"
    currencies = ", ".join(sorted({r["currency"] for r in results}))
    return f"offers {len(results)}: {'; '.join(one(r) for r in results)} (currencies: {currencies})"


def _status(row: Row) -> str:
    p = row.position
    if not p:
        return "no position"
    state = p["price_status"] + (
        f" ({p['price_status_detail']})" if p["price_status_detail"] else ""
    )
    if p["price"] is None:
        return f"{state} · no price: estimated at cost"
    stale = "stale" if p["price_stale"] else "not stale"
    return f"{state} · {p['price']} {row.entry.currency} on {p['price_date']} · {stale}"


def _seconds(run: JobRun) -> str:
    if run.finished_at is None:
        return "not finished"
    return f"in {(run.finished_at - run.started_at).total_seconds():.0f} s"


def format_report(o: Outcome) -> list[str]:
    calls = o.transport.calls
    runs = "; ".join(f"run {r.kind}: {r.result} {_seconds(r)}" for r in o.runs) or "no run"
    hosts = ", ".join(f"{host} {n}" for host, n in sorted(calls.items())) or "none"
    gaps = ", ".join(f"{host} {s:.1f} s" for host, s in sorted(o.transport.gaps.items()))
    lines = [
        f"Live check · {o.started:%Y-%m-%d %H:%M} UTC · one pass · {runs}",
        f"Calls: {calls.total()} of at most {o.most} ({hosts})",
        f"Shortest gap: {gaps or 'n/a'}",
        f"stopped: {o.stopped}" if o.stopped else "No 429, no 503, under the cap.",
    ]
    for row in o.rows:
        e, listing = row.entry, row.listing
        lines += ["", f"{e.isin}  {e.name} · {e.asset_type} in {e.currency}"]
        if isinstance(row.offered, list):
            lines.append(f"  search    {_offered(row.offered)}")
        elif row.offered:
            lines.append(f"  search    {row.offered}")
        source = f", from {row.class_source}" if row.class_source else ""
        lines.append(f"  class     {row.price_class or 'unknown'}{source}")
        lines.append(f"  checks    {', '.join(row.checks) or 'none'}")
        if listing is None:
            lines.append(f"  listing   none · {_verdict(e, listing)}")
        else:
            pence = " in pence (stored ÷100)" if listing.unit == "MINOR_CURRENCY_UNIT" else ""
            lines.append(
                f"  listing   {listing.symbol} · {listing.exchange_code or '?'} · "
                f"{listing.currency}{pence} · {_verdict(e, listing)}"
            )
        lines.append(f"  status    {_status(row)}")
    lines.append("")
    if isinstance(o.text, list):
        with_isin = sum(1 for r in o.text if r["isin"])
        noun = "result" if len(o.text) == 1 else "results"
        isin = "none with an ISIN" if not with_isin else f"{with_isin} with an ISIN"
        lines.append(f'Text search "{o.text_query}": {len(o.text)} {noun}, {isin}')
    elif o.text:
        lines.append(f'Text search "{o.text_query}": {o.text}')
    for currency, first, last, days, rate in o.fx:
        lines.append(f"FX {currency}: {days} days, {first} … {last}, last {rate}")
    for currency in sorted({r.entry.currency for r in o.rows} - {"EUR"} - {f[0] for f in o.fx}):
        lines.append(f"FX {currency}: no rates")
    errors = Counter()
    for r in o.runs:
        errors.update(r.errors or {})
    lines.append(
        "Run errors: " + (", ".join(f"{c} {n}" for c, n in sorted(errors.items())) or "none")
    )
    lines.append(f"Folder kept: {o.folder}" if o.kept else "Folder deleted.")
    return lines


# --- the command --------------------------------------------------------------------------

_EXTRA = re.compile(r"([A-Za-z0-9]{12}):([A-Z]{3})")


def _extras(values: Sequence[str]) -> list[Entry]:
    if len(values) > MAX_EXTRAS:
        raise ValueError(f"at most {MAX_EXTRAS} --extra per run (each adds up to {PER_ISIN} calls)")
    listed = {e.isin for e in ENTRIES}  # one asset per ISIN and user, in any currency
    found: list[Entry] = []
    for n, value in enumerate(values, start=1):
        match = _EXTRA.fullmatch(value)
        if match is None:
            raise ValueError(f"--extra takes ISIN:CURRENCY, the currency in capitals: {value!r}")
        isin, currency = match.group(1).upper(), match.group(2)
        if not valid_isin(isin):
            raise ValueError(f"{isin} is not a valid ISIN (check digit)")
        if isin in listed:
            raise ValueError(f"{isin} is already in the list")
        listed.add(isin)
        found.append(extra_entry(n, isin, currency))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="ISIN:CURRENCY",
        help=f"one more public security for this run only (at most {MAX_EXTRAS})",
    )
    parser.add_argument("--keep", action="store_true", help="keep the data folder at the end")
    args = parser.parse_args(argv)  # fmt: skip
    if os.environ.get("PT_LIVE_CHECK") != "1":
        print("refused: set PT_LIVE_CHECK=1 to run the live check (it calls Yahoo, OpenFIGI "
              "and Frankfurter)")  # fmt: skip
        return 2
    try:
        extras = _extras(args.extra)
    except ValueError as exc:
        print(f"refused: {exc}")
        return 2
    if not os.environ.get("PT_DATA_DIR"):
        print("refused: set PT_DATA_DIR to a new folder whose name starts with .data, "
              "e.g. /tmp/.data-live-check")  # fmt: skip
        return 2
    # The app's own logs only from warnings up (a rate limit, a failing engine): the report
    # is the output.
    settings = Settings().model_copy(update={"log_level": "WARNING"})
    folder = settings.data_dir
    if not folder.resolve().name.startswith(".data"):
        print(f"refused: {folder}: the folder name must start with .data")
        return 2
    if folder.exists():
        print(f"refused: {folder} exists: the folder must not exist yet (the check creates it "
              "and deletes it at the end)")  # fmt: skip
        return 2
    return run(settings, ENTRIES, UrllibTransport(), extras=extras, keep=args.keep)


if __name__ == "__main__":
    sys.exit(main())
