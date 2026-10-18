"""The plug-in proof (D-117, D-131, `docs/adding-a-provider.md`): two engines that exist only
in this file, each an adapter plus one registration line, take part end to end with no change
anywhere else in the app.

- `keyed`, a second `ETF_STOCK` engine that needs a credential: the credential goes through
  the encrypted store, reaches the adapter only, and never a log, a response or the job log;
  when the first engine does not know an ISIN, `keyed` gets the listing, back-fills it, and
  the position is valued from its closes through the API. Without its credential it is
  skipped, and the other engines still run.
- `bondy`, a `BOND`-only engine quoting in % of nominal: a bond no classifier knows goes
  through the `BOND` order first and gets its listing and class from it (D-133).

Invented ISINs, symbols, closes and credential."""

import logging
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from engines import EQUITY_UNITS
from fakes import InstantTime, Provider
from fastapi.testclient import TestClient
from helpers import fake_isin
from sqlalchemy import select
from test_api_reports import BOND, ETF, get, post, tx

from portfolio_tracker.db import db_transaction, models
from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.base import (
    Capabilities,
    Close,
    CloseSeries,
    Listing,
    Role,
    SearchQuery,
)
from portfolio_tracker.marketdata.registry import (
    ProviderContext,
    ProviderSpec,
    Registry,
    default_registry,
)
from portfolio_tracker.marketdata.routing import ProviderConfigError
from portfolio_tracker.repositories import credentials, instruments
from portfolio_tracker.services.scheduler import market_job

SECRET = "invented-api-key-7f3a"
KEYED_ISIN, BOND_ISIN = fake_isin(61), fake_isin(62)
NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)  # the app clock of the tests
LAST = date(2026, 1, 14)


# --- the two engines: an adapter each ----------------------------------------------------------


class KeyedEngine:
    """An ETF/stock engine behind a key: it knows one ISIN, on an exchange of its own."""

    name = "keyed"
    capabilities = Capabilities(
        roles=frozenset({Role.ETF_STOCK}),
        search_isin=True,
        units=EQUITY_UNITS,
        needs_credential=True,
        credential_label="api_key",
        main_exchanges=("KEX",),
    )

    def __init__(self, context: ProviderContext) -> None:
        self.context = context
        built.append(self)

    def search(self, query: SearchQuery, *, max_wait: float | None = None) -> list[Listing]:
        def answer() -> list[Listing]:
            if query.isin != KEYED_ISIN:
                return []
            return [Listing("keyed", "KEYW", "EUR", QuoteUnit.CURRENCY, name="Keyed World",
                            exchange="Keyed Exchange", exchange_code="KEX",
                            last_close_day=LAST)]  # fmt: skip

        return self.context.throttle.call(answer, max_wait=max_wait)

    def daily_closes(self, symbol: str, start: date, end: date) -> CloseSeries:
        closes = (Close(date(2026, 1, 13), Decimal("12.10")), Close(LAST, Decimal("12.34")))
        return self.context.throttle.call(lambda: CloseSeries("EUR", QuoteUnit.CURRENCY, closes))


class BondEngine:
    """A bond engine: % of nominal only."""

    name = "bondy"
    capabilities = Capabilities(
        roles=frozenset({Role.BOND}),
        search_isin=True,
        units=frozenset({QuoteUnit.PERCENT_OF_NOMINAL}),
    )

    def __init__(self, context: ProviderContext) -> None:
        self.context = context

    def search(self, query: SearchQuery, *, max_wait: float | None = None) -> list[Listing]:
        if query.isin != BOND_ISIN:
            return []
        unit = QuoteUnit.PERCENT_OF_NOMINAL
        return [Listing("bondy", "EXB30", "EUR", unit, exchange_code="BVX", last_close_day=LAST)]

    def daily_closes(self, symbol: str, start: date, end: date) -> CloseSeries:
        return CloseSeries("EUR", QuoteUnit.PERCENT_OF_NOMINAL, (Close(LAST, Decimal("98.20")),))


built: list[KeyedEngine] = []


def registry_with_both() -> Registry:
    """The shipped engines plus one registration line per new engine."""
    time = InstantTime()
    registry = default_registry(time.clock, time.sleep)
    registry.register(ProviderSpec("keyed", KeyedEngine, KeyedEngine.capabilities))
    registry.register(ProviderSpec("bondy", BondEngine, BondEngine.capabilities))
    return registry


# --- the app with both engines configured ------------------------------------------------------


@pytest.fixture
def app(market_app_with, monkeypatch: pytest.MonkeyPatch):
    """Yahoo knows neither ISIN; `keyed` comes second for ETFs and stocks, `bondy` alone for
    bonds (environment values seeded at the first start). Jobs run in the tests."""
    built.clear()
    answers = Provider(unknown=frozenset({KEYED_ISIN, BOND_ISIN}))
    start = market_app_with(
        answers,
        registry_with_both(),
        price_providers="yahoo,keyed",
        bond_price_providers="bondy",
    )
    with start as client:
        monkeypatch.setattr(client.app.state.market_data.scheduler, "trigger", lambda kind: None)
        yield client


def run(client: TestClient) -> object:
    market_data = client.app.state.market_data
    return market_job(
        client.app.state.engine,
        market_data.registry,
        market_data.transport,
        market_data.cipher,
        urls=market_data.urls,
        clock=lambda: NOW,
    )("DAILY")


def store_credential(client: TestClient, value: str = SECRET) -> None:
    with db_transaction(client.app.state.engine, write=True) as session:
        credentials.set_credential(session, client.app.state.credential_cipher, "keyed", value)


def holding(client: TestClient, body: dict, quantity: str) -> dict:
    account = post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})
    asset = post(client, "/api/v1/assets", body)
    tx(client, account, "BUY", "2025-06-02", asset_id=asset["id"], quantity=quantity,
       unit_price="10.00", gross_amount="100.00", total_amount="100.00",
       affects_cash=False)  # fmt: skip
    return account


def active_of(client: TestClient, isin: str):
    with db_transaction(client.app.state.engine, write=False) as session:
        instrument = instruments.get_instrument(session, isin, "EUR")
        listing = instruments.active_listing(session, instrument.id)
        return instrument.price_class, instrument.class_source, listing.provider, listing.symbol


# --- the keyed engine -------------------------------------------------------------------------


def test_a_keyed_engine_takes_over_end_to_end_and_its_key_never_shows(
    app: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    store_credential(app)
    account = holding(app, ETF | {"isin": KEYED_ISIN}, "10")

    with caplog.at_level(logging.DEBUG):
        summary = run(app)
        [position] = get(app, f"/api/v1/accounts/{account['id']}/positions")
        status = app.get("/api/v1/admin/market-data/status")

    assert [engine.context.credential for engine in built] == [SECRET]  # the adapter only
    assert active_of(app, KEYED_ISIN) == ("ETF_STOCK", "UNIT", "keyed", "KEYW")
    assert summary.counts["listings_chosen"] == 1 and summary.counts["series_updated"] == 1
    assert (position["price"], position["price_date"], position["price_source"]) == (
        "12.34", "2026-01-14", "PROVIDER",
    )  # fmt: skip
    assert Decimal(position["value"]) == Decimal("123.40")

    logged = caplog.text + "".join(repr(r.__dict__) for r in caplog.records)
    assert SECRET not in logged
    assert SECRET not in status.text and SECRET not in repr(summary)
    with db_transaction(app.app.state.engine, write=False) as session:
        stored = session.get(models.ProviderCredential, "keyed")
        runs = session.scalars(select(models.JobRun)).all()
    assert SECRET.encode() not in stored.ciphertext + stored.nonce  # encrypted at rest
    assert all(SECRET not in repr((r.counts, r.errors)) for r in runs)


def test_without_its_credential_the_keyed_engine_is_skipped_and_named_only(
    app: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    holding(app, ETF | {"isin": KEYED_ISIN}, "10")
    with caplog.at_level(logging.WARNING):
        summary = run(app)
    assert built == [] and summary.result in ("OK", "PARTIAL")
    (record,) = [r for r in caplog.records if r.message == "provider skipped: no credential"]
    assert record.provider == "keyed"

    store_credential(app)  # entered later: the next run uses it
    run(app)
    assert active_of(app, KEYED_ISIN)[2:] == ("keyed", "KEYW")


def test_a_credential_that_no_longer_decrypts_counts_as_missing(
    app: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    store_credential(app)
    with db_transaction(app.app.state.engine, write=True) as session:
        row = session.get(models.ProviderCredential, "keyed")
        row.ciphertext = bytes(len(row.ciphertext))  # altered data
    with caplog.at_level(logging.WARNING):
        run(app)
    assert built == [] and "credential cannot be read" in caplog.text
    assert SECRET not in caplog.text


# --- the bond engine --------------------------------------------------------------------------


def test_a_bond_resolves_through_the_bond_order_of_its_own_engine(app: TestClient) -> None:
    account = holding(app, BOND | {"isin": BOND_ISIN}, "5000")

    run(app)

    assert active_of(app, BOND_ISIN) == ("BOND", "UNIT", "bondy", "EXB30")
    [position] = get(app, f"/api/v1/accounts/{account['id']}/positions")
    assert (position["price"], position["price_source"], position["price_status"]) == (
        "98.20", "PROVIDER", "OK",
    )  # fmt: skip
    assert Decimal(position["value"]) == Decimal(4910)  # 5000 x 98.20 / 100


def test_the_engines_are_placed_only_where_they_serve() -> None:
    """`bondy` cannot be put in the ETF/stock order, nor `keyed` in the bond order: the
    administrator can only place an engine where it works (D-131)."""
    registry = registry_with_both()
    assert registry.check_order(Role.BOND, ("bondy",)) == ("bondy",)
    with pytest.raises(ProviderConfigError, match="does not serve"):
        registry.check_order(Role.ETF_STOCK, ("yahoo", "bondy"))
    with pytest.raises(ProviderConfigError, match="does not serve"):
        registry.check_order(Role.BOND, ("keyed",))
