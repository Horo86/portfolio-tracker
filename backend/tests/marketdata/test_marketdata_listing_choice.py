"""Linking, classification and the choice of an instrument's listing (D-121, D-125, D-133,
D-134, D-136), with fake engines and a migrated database file. ISINs are invented (`ZZ`)."""

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from helpers import fake_isin
from sqlalchemy import Engine

from portfolio_tracker.db import db_transaction, models
from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.base import (
    Capabilities,
    ErrorCode,
    Identifier,
    Listing,
    PriceClass,
    ProviderError,
    Role,
    SearchQuery,
    SecurityFacts,
)
from portfolio_tracker.marketdata.registry import BuiltEngines
from portfolio_tracker.repositories import assets, instruments, users
from portfolio_tracker.services.market_data import RETRY_DAYS, choose_listings, link_assets

NOW = datetime(2026, 3, 6, 18, 0, tzinfo=UTC)
RECENT = date(2026, 3, 5)
ETF_ISIN, BOND_ISIN = fake_isin(51), fake_isin(52)
EQUITY_UNITS = frozenset({QuoteUnit.CURRENCY, QuoteUnit.MINOR_CURRENCY_UNIT})
EQUITY = Capabilities(
    roles=frozenset({Role.ETF_STOCK}),
    search_isin=True,
    units=EQUITY_UNITS,
    main_exchanges=("MIL", "GER", "LSE"),
)
BONDS = Capabilities(
    roles=frozenset({Role.BOND}),
    search_isin=True,
    units=frozenset({QuoteUnit.PERCENT_OF_NOMINAL}),
)


class FakePrices:
    """A price engine answering from a table by ISIN; records every query."""

    def __init__(
        self,
        name: str,
        answers: dict[str, list[Listing] | ProviderError],
        capabilities: Capabilities = EQUITY,
        during: Callable[[], None] | None = None,
    ) -> None:
        self.name, self.capabilities, self.answers, self.during = (
            name,
            capabilities,
            answers,
            during,
        )
        self.queries: list[SearchQuery] = []

    def search(self, query: SearchQuery, *, max_wait: float | None = None) -> list[Listing]:
        self.queries.append(query)
        if self.during:
            self.during()
        answer = self.answers.get(query.isin or "", [])
        if isinstance(answer, ProviderError):
            raise answer
        return list(answer)

    def daily_closes(self, symbol: str, start: date, end: date):
        raise AssertionError("closes are not fetched when a listing is chosen")


class FakeClassifier:
    def __init__(
        self,
        name: str,
        answers: dict[str, SecurityFacts | ProviderError | None],
        *,
        echoes_isin: bool = True,
        during: Callable[[], None] | None = None,
    ) -> None:
        self.name, self.answers, self.during = name, answers, during
        self.capabilities = Capabilities(roles=frozenset({Role.CLASSIFY}), echoes_isin=echoes_isin)
        self.asked: list[str] = []

    def classify(self, isin: str) -> SecurityFacts | None:
        self.asked.append(isin)
        if self.during:
            self.during()
        answer = self.answers.get(isin)
        if isinstance(answer, ProviderError):
            raise answer
        return answer


def built(etf=(), bond=(), classify=()) -> BuiltEngines:
    return BuiltEngines(
        {
            Role.ETF_STOCK: tuple(etf),
            Role.BOND: tuple(bond),
            Role.FX: (),
            Role.CLASSIFY: tuple(classify),
        }
    )


def listing(
    symbol: str,
    code: str | None,
    *,
    provider: str = "alpha",
    currency: str = "EUR",
    unit: QuoteUnit = QuoteUnit.CURRENCY,
    last: date | None = RECENT,
    hint: Identifier | None = None,
) -> Listing:
    return Listing(
        provider=provider,
        symbol=symbol,
        currency=currency,
        unit=unit,
        exchange=code.title() if code else None,
        exchange_code=code,
        last_close_day=last,
        hint=hint,
    )


def facts(isin: str, price_class: PriceClass | None, *hints: Identifier) -> SecurityFacts:
    return SecurityFacts(isin, price_class, tuple(hints))


def holdings(engine: Engine, *rows: tuple[str, str | None, str]) -> dict[str, models.Asset]:
    """(owner, ISIN, quote currency) → that owner's asset, keyed "owner:ISIN:currency"."""
    made: dict[str, models.Asset] = {}
    with db_transaction(engine, write=True) as session:
        owners: dict[str, models.User] = {}
        for owner, isin, currency in rows:
            if owner not in owners:
                owners[owner] = users.add_user(
                    session, username=owner, role="INVESTOR", password_hash="x", language="en"
                )
            made[f"{owner}:{isin}:{currency}"] = assets.add_private_asset(
                session,
                owners[owner].id,
                isin=isin,
                name="Example",
                asset_type="ETF",
                quote_currency=currency,
                quote_unit="CURRENCY",
            )
    return made


def stored(engine: Engine, isin: str = ETF_ISIN, currency: str = "EUR"):
    """(instrument, its listings by engine)."""
    with db_transaction(engine, write=False) as session:
        instrument = instruments.get_instrument(session, isin, currency)
        assert instrument is not None
        rows = {row.provider: row for row in instruments.listings(session, instrument.id)}
    return instrument, rows


# --- linking (D-116, D-125) -------------------------------------------------------------------


def test_same_isin_and_currency_share_one_instrument_another_currency_does_not(
    db_engine: Engine,
) -> None:
    """Two users holding one ISIN in EUR share an instrument; a third holding it in USD gets
    another one. (One user holds an ISIN as one asset, whatever the accounts: M2 index.)"""
    made = holdings(
        db_engine,
        ("alice", ETF_ISIN, "EUR"),
        ("bob", ETF_ISIN, "EUR"),
        ("carol", ETF_ISIN, "USD"),
        ("carol", None, "EUR"),
    )
    assert link_assets(db_engine) == (3, 2)
    assert link_assets(db_engine) == (0, 0)  # nothing left to link

    with db_transaction(db_engine, write=False) as session:
        linked = {key: session.get(models.Asset, a.id).instrument_id for key, a in made.items()}
    alice, bob = linked[f"alice:{ETF_ISIN}:EUR"], linked[f"bob:{ETF_ISIN}:EUR"]
    assert alice is not None and alice == bob
    assert linked[f"carol:{ETF_ISIN}:USD"] not in (None, alice)
    assert linked["carol:None:EUR"] is None  # no ISIN: manual prices only


# --- the class and the listing ----------------------------------------------------------------


def test_an_etf_gets_its_class_and_the_first_accepted_listing_in_rank(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    milan = Identifier("XMPWM", "MTAA", "ETP", "EXAMPLE WORLD")
    openfigi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK, milan)})
    alpha = FakePrices(
        "alpha",
        {
            ETF_ISIN: [
                listing("0XMP.IL", "IOB"),  # the engine lists an order book line first
                listing("XMPW.DE", "GER"),
                listing("XMPWM.MI", "MIL", hint=milan),
            ]
        },
    )
    result = choose_listings(db_engine, built([alpha], classify=[openfigi]), (), NOW)

    assert (result.linked, result.created, result.chosen) == (1, 1, 1)
    assert result.classified == {"figi": 1}
    (query,) = alpha.queries
    assert query == SearchQuery.by_isin(ETF_ISIN, (milan,))
    instrument, rows = stored(db_engine)
    assert (instrument.price_class, instrument.class_source) == ("ETF_STOCK", "figi")
    chosen = rows["alpha"]
    assert (chosen.symbol, chosen.exchange_code, chosen.matched_by) == ("XMPWM.MI", "MIL", "HINT")
    assert (chosen.status, chosen.is_active, chosen.activated_at) == ("OK", True, NOW)
    assert (chosen.currency, chosen.unit, chosen.checked_at) == ("EUR", "CURRENCY", NOW)


def test_preferred_exchanges_go_to_the_engine_and_decide(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL"), listing("XMPW.L", "LSE")]})
    choose_listings(db_engine, built([alpha]), ("lse", "MIL"), NOW)
    assert alpha.queries[0].exchanges == ("lse", "MIL")
    assert stored(db_engine)[1]["alpha"].symbol == "XMPW.L"


def test_a_bond_without_a_bond_engine_is_never_sent_to_the_equity_engine(
    db_engine: Engine,
) -> None:
    holdings(db_engine, ("alice", BOND_ISIN, "EUR"))
    openfigi = FakeClassifier("figi", {BOND_ISIN: facts(BOND_ISIN, PriceClass.BOND)})
    alpha = FakePrices("alpha", {BOND_ISIN: [listing("XMPB.MI", "MIL")]})
    engines = built([alpha], classify=[openfigi])

    first = choose_listings(db_engine, engines, (), NOW)
    second = choose_listings(db_engine, engines, (), NOW + timedelta(days=1))

    assert alpha.queries == []
    assert (first.no_engine, first.classified) == (1, {"figi": 1})
    assert second.no_engine == 1 and openfigi.asked == [BOND_ISIN]  # not asked again
    instrument, rows = stored(db_engine, BOND_ISIN)
    assert instrument.price_class == "BOND" and rows == {}  # manual prices


def test_a_bond_gets_its_listing_from_the_bond_engine(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", BOND_ISIN, "EUR"))
    openfigi = FakeClassifier("figi", {BOND_ISIN: facts(BOND_ISIN, PriceClass.BOND)})
    alpha = FakePrices("alpha", {BOND_ISIN: [listing("XMPB.MI", "MIL")]})
    bonds = FakePrices(
        "bonds",
        {BOND_ISIN: [listing("XMPB", None, provider="bonds", unit=QuoteUnit.PERCENT_OF_NOMINAL)]},
        BONDS,
    )
    choose_listings(db_engine, built([alpha], [bonds], [openfigi]), (), NOW)
    rows = stored(db_engine, BOND_ISIN)[1]
    assert set(rows) == {"bonds"} and rows["bonds"].is_active
    assert rows["bonds"].unit == "PERCENT_OF_NOMINAL" and alpha.queries == []


def test_without_a_class_the_bond_order_comes_first_and_the_unit_sets_the_class(
    db_engine: Engine,
) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    unknown = FakeClassifier("figi", {ETF_ISIN: None})
    bonds = FakePrices("bonds", {}, BONDS)
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})

    result = choose_listings(db_engine, built([alpha], [bonds], [unknown]), (), NOW)

    assert len(bonds.queries) == 1 and len(alpha.queries) == 1
    assert result.classified == {"UNIT": 1}
    instrument, rows = stored(db_engine)
    assert (instrument.price_class, instrument.class_source) == ("ETF_STOCK", "UNIT")
    assert rows["bonds"].status == "NOT_FOUND" and rows["alpha"].is_active


def test_without_a_class_a_bond_engine_listing_in_percent_makes_it_a_bond(
    db_engine: Engine,
) -> None:
    holdings(db_engine, ("alice", BOND_ISIN, "EUR"))
    bonds = FakePrices(
        "bonds",
        {BOND_ISIN: [listing("XMPB", None, provider="bonds", unit=QuoteUnit.PERCENT_OF_NOMINAL)]},
        BONDS,
    )
    alpha = FakePrices("alpha", {BOND_ISIN: [listing("XMPB.MI", "MIL")]})
    choose_listings(db_engine, built([alpha], [bonds]), (), NOW)  # no classifier at all
    instrument, rows = stored(db_engine, BOND_ISIN)
    assert (instrument.price_class, instrument.class_source) == ("BOND", "UNIT")
    assert alpha.queries == [] and rows["bonds"].is_active


def test_a_classifier_error_postpones_the_instrument(db_engine: Engine) -> None:
    """A network error must not let a unit decide the class: a bond could reach Yahoo."""
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    down = FakeClassifier("figi", {ETF_ISIN: ProviderError(ErrorCode.NETWORK, "timeout")})
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})

    result = choose_listings(db_engine, built([alpha], classify=[down]), (), NOW)
    assert (result.postponed, result.errors) == (1, {"NETWORK": 1})
    assert alpha.queries == [] and stored(db_engine)[1] == {}

    down.answers[ETF_ISIN] = facts(ETF_ISIN, PriceClass.ETF_STOCK)
    later = choose_listings(db_engine, built([alpha], classify=[down]), (), NOW)
    assert later.chosen == 1 and stored(db_engine)[0].class_source == "figi"


def test_a_later_classifier_answers_when_the_first_fails(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    down = FakeClassifier("figi", {ETF_ISIN: ProviderError(ErrorCode.NETWORK, "timeout")})
    yahoo = FakeClassifier("yahoo", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)})
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})
    result = choose_listings(db_engine, built([alpha], classify=[down, yahoo]), (), NOW)
    assert result.chosen == 1 and stored(db_engine)[0].class_source == "yahoo"


def test_hints_come_only_from_a_classifier_that_echoes_the_isin(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    hint = Identifier("XMPW", "XETR", "ETP")
    guess = FakeClassifier(
        "guess", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK, hint)}, echoes_isin=False
    )
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})
    choose_listings(db_engine, built([alpha], classify=[guess]), (), NOW)
    assert alpha.queries[0].hints == ()


# --- what is recorded, and when an engine is asked again --------------------------------------


def test_a_failing_engine_is_recorded_and_the_next_one_is_tried(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    alpha = FakePrices("alpha", {ETF_ISIN: ProviderError(ErrorCode.RATE_LIMITED, "paused")})
    beta = FakePrices("beta", {ETF_ISIN: [listing("XMPW.MI", "MIL", provider="beta")]})

    result = choose_listings(db_engine, built([alpha, beta]), (), NOW)

    assert (result.chosen, result.errors) == (1, {"RATE_LIMITED": 1})
    rows = stored(db_engine)[1]
    assert (rows["alpha"].status, rows["alpha"].last_error_code) == ("FAILING", "RATE_LIMITED")
    assert rows["alpha"].failure_count == 1 and rows["beta"].is_active


def test_failing_engines_are_asked_again_on_the_next_run(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    alpha = FakePrices("alpha", {ETF_ISIN: ProviderError(ErrorCode.HTTP_ERROR, "503")})
    first = choose_listings(db_engine, built([alpha]), (), NOW)
    second = choose_listings(db_engine, built([alpha]), (), NOW)
    assert first.not_found == second.not_found == 0  # a failure is not "not found"
    assert len(alpha.queries) == 2 and stored(db_engine)[1]["alpha"].failure_count == 2

    alpha.answers[ETF_ISIN] = [listing("XMPW.MI", "MIL")]
    choose_listings(db_engine, built([alpha]), (), NOW)
    row = stored(db_engine)[1]["alpha"]
    assert (row.status, row.failure_count, row.last_error_code, row.is_active) == (
        "OK",
        0,
        None,
        True,
    )


@pytest.mark.parametrize(
    ("candidates", "status", "mismatch"),
    [
        ([], "NOT_FOUND", None),
        ([listing("XMPW", "NMS", currency="USD")], "MISMATCH", "CURRENCY"),
        ([listing("XMPW.MI", "MIL", unit=QuoteUnit.PERCENT_OF_NOMINAL)], "MISMATCH", "UNIT"),
        ([listing("XMPW.MI", "MIL", last=date(2026, 2, 20))], "NOT_FOUND", None),  # stale
        ([listing("XMPW.MI", "MIL", last=None)], "NOT_FOUND", None),
    ],
)
def test_unfit_candidates_are_recorded_and_asked_again_after_30_days(
    db_engine: Engine, candidates: list[Listing], status: str, mismatch: str | None
) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    alpha = FakePrices("alpha", {ETF_ISIN: candidates})
    engines = built([alpha], classify=[FakeClassifier("figi", {})])

    first = choose_listings(db_engine, engines, (), NOW)
    row = stored(db_engine)[1]["alpha"]
    assert (row.status, row.mismatch, row.symbol, row.is_active) == (status, mismatch, None, False)
    assert first.not_found == 1

    soon = choose_listings(db_engine, engines, (), NOW + timedelta(days=RETRY_DAYS - 1))
    assert soon.no_engine == 1 and len(alpha.queries) == 1  # no call, classifier included

    choose_listings(db_engine, engines, (), NOW + timedelta(days=RETRY_DAYS))
    assert len(alpha.queries) == 2


def test_an_instrument_with_an_active_listing_is_left_alone(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"), ("bob", ETF_ISIN, "EUR"))
    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)})
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})
    engines = built([alpha], classify=[figi])
    choose_listings(db_engine, engines, (), NOW)
    holdings(db_engine, ("carol", ETF_ISIN, "EUR"))  # a later holder of the same security

    again = choose_listings(db_engine, engines, (), NOW + timedelta(days=1))

    assert (again.linked, again.created, again.chosen) == (1, 0, 0)
    assert len(alpha.queries) == 1 and figi.asked == [ETF_ISIN]  # one search for everyone


# --- network discipline and privacy -----------------------------------------------------------


def test_no_transaction_is_open_while_engines_are_called(db_engine: Engine, tmp_path: Path) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    checked: list[str] = []

    def take_the_write_lock_elsewhere() -> None:
        other = sqlite3.connect(tmp_path / "db" / "app.sqlite", timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")  # fails at once if anyone holds the lock
            other.execute("ROLLBACK")
        finally:
            other.close()
        assert db_engine.pool.checkedout() == 0
        checked.append("ok")

    figi = FakeClassifier(
        "figi",
        {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)},
        during=take_the_write_lock_elsewhere,
    )
    alpha = FakePrices(
        "alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]}, during=take_the_write_lock_elsewhere
    )
    choose_listings(db_engine, built([alpha], classify=[figi]), (), NOW)
    assert checked == ["ok", "ok"]


def test_no_isin_reaches_the_logs_or_the_result(
    db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"), ("alice", BOND_ISIN, "EUR"))
    down = FakeClassifier("figi", {BOND_ISIN: ProviderError(ErrorCode.NETWORK, "timeout")})
    alpha = FakePrices("alpha", {ETF_ISIN: ProviderError(ErrorCode.HTTP_ERROR, "503")})
    with caplog.at_level(logging.DEBUG):
        result = choose_listings(db_engine, built([alpha], classify=[down]), (), NOW)
    text = caplog.text + "".join(repr(r.__dict__) for r in caplog.records) + repr(result)
    assert "classifier failed" in caplog.text and "listing search failed" in caplog.text
    assert ETF_ISIN not in text and BOND_ISIN not in text
