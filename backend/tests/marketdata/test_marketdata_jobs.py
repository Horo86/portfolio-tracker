"""One market data job (D-121, D-132, D-134): the job log, the re-check request, the class
re-check, FX in the run, and each asset's price status. Fake engines and clock, a migrated
database file, invented ISINs, symbols, closes and rates."""

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest
from engines import (
    BOND_ISIN,
    ETF_ISIN,
    NOW,
    FakeClassifier,
    FakePrices,
    built,
    facts,
    holdings,
    listing,
)
from fakes import FakeFxProvider
from sqlalchemy import Engine, select

from portfolio_tracker.db import db_transaction, models
from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.base import (
    Close,
    CloseSeries,
    ErrorCode,
    PriceClass,
    ProviderError,
    Role,
)
from portfolio_tracker.marketdata.registry import BuiltEngines
from portfolio_tracker.marketdata.routing import RoutingConfig
from portfolio_tracker.repositories import accounts, instruments, job_runs, market_config, users
from portfolio_tracker.repositories.prices import PriceState, PriceStatus, price_statuses
from portfolio_tracker.services import market_jobs
from portfolio_tracker.services.market_jobs import run_job

CLOSES = CloseSeries("EUR", QuoteUnit.CURRENCY, (Close(date(2026, 3, 5), Decimal("10.30")),))


def clock(*moments):
    """A clock answering `moments` in turn (start, finish, ...), then the last one."""
    remaining = list(moments)
    return lambda: remaining.pop(0) if len(remaining) > 1 else remaining[0]


def alpha() -> FakePrices:
    engine = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})
    engine.closes["XMPW.MI"] = CLOSES
    return engine


def with_fx(engines: BuiltEngines, fx: FakeFxProvider) -> BuiltEngines:
    return BuiltEngines({**engines.by_role, Role.FX: (fx,)})


def runs(engine: Engine) -> list[models.JobRun]:
    with db_transaction(engine, write=False) as session:
        return list(session.scalars(select(models.JobRun).order_by(models.JobRun.started_at)))


def seed_config(engine: Engine) -> None:
    with db_transaction(engine, write=True) as session:
        market_config.seed_if_empty(session, RoutingConfig(frozenset()))


# --- the job log (D-121) ----------------------------------------------------------------------


def test_a_run_is_logged_with_counts_and_codes_only(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    done = NOW + timedelta(minutes=2)

    summary = run_job(db_engine, built([alpha()]), (), "DAILY", clock(NOW, done))

    (run,) = runs(db_engine)
    assert (run.kind, run.result, run.started_at, run.finished_at) == ("DAILY", "OK", NOW, done)
    assert run.counts == {
        "assets_linked": 1,
        "instruments_created": 1,
        "listings_chosen": 1,
        "classified": 1,  # from the unit: no classifier in this run
        "series_updated": 1,
        "closes_stored": 1,
    }
    assert run.errors == {} and summary.result == "OK" and not summary.recheck
    assert ETF_ISIN not in repr(run.counts) + repr(run.errors)
    with db_transaction(db_engine, write=False) as session:
        assert job_runs.last_run(session).id == run.id


def test_provider_errors_make_a_run_partial(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    failing = alpha()
    failing.closes["XMPW.MI"] = ProviderError(ErrorCode.RATE_LIMITED, "paused")
    summary = run_job(db_engine, built([failing]), (), "MANUAL", clock(NOW))
    assert (summary.result, summary.errors) == ("PARTIAL", {"RATE_LIMITED": 1})
    assert runs(db_engine)[0].errors == {"RATE_LIMITED": 1}


def test_an_unexpected_error_fails_the_run_without_raising(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*args, **kwargs):
        raise RuntimeError(f"bad {ETF_ISIN}")

    monkeypatch.setattr(market_jobs, "refresh_prices", broken)
    with caplog.at_level(logging.DEBUG):
        summary = run_job(db_engine, built(), (), "DAILY", clock(NOW))
    assert (summary.result, summary.errors) == ("FAILED", {"RuntimeError": 1})
    assert runs(db_engine)[0].result == "FAILED"
    text = caplog.text + "".join(repr(r.__dict__) for r in caplog.records)
    assert "market data job failed" in caplog.text and ETF_ISIN not in text


def test_an_unknown_kind_is_refused(db_engine: Engine) -> None:
    with pytest.raises(ValueError, match="unknown job kind"):
        run_job(db_engine, built(), (), "WEEKLY", clock(NOW))
    assert runs(db_engine) == []


def test_fx_rates_are_updated_in_the_run(db_engine: Engine) -> None:
    with db_transaction(db_engine, write=True) as session:
        alice = users.add_user(
            session, username="alice", role="INVESTOR", password_hash="x", language="en"
        )
        accounts.add_account(session, alice.id, name="Broker", currency="USD")
    fx = FakeFxProvider({"USD": {date(2026, 3, 5): Decimal("1.08")}})
    summary = run_job(db_engine, with_fx(built(), fx), (), "DAILY", clock(NOW))
    assert summary.counts == {"fx_currencies_updated": 1, "fx_rates_stored": 1}


# --- the re-check request (D-132, D-134) ------------------------------------------------------


def test_a_pending_request_makes_the_run_a_re_check_and_is_cleared(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    seed_config(db_engine)
    engine = alpha()
    run_job(db_engine, built([engine]), (), "DAILY", clock(NOW))
    with db_transaction(db_engine, write=True) as session:
        market_config.request_resolve(session, NOW + timedelta(hours=1))

    later = NOW + timedelta(days=1)
    summary = run_job(db_engine, built([engine]), (), "DAILY", clock(later))

    assert summary.recheck and len(engine.queries) == 2  # the listed engine searched again
    with db_transaction(db_engine, write=False) as session:
        assert market_config.resolve_requested_at(session) is None


def test_a_request_made_during_a_run_stays_pending(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    seed_config(db_engine)
    during = NOW + timedelta(seconds=30)

    class RequestsWhileSearching(FakePrices):
        def search(self, query, *, max_wait=None):
            with db_transaction(db_engine, write=True) as session:
                market_config.request_resolve(session, during)
            return super().search(query, max_wait=max_wait)

    engine = RequestsWhileSearching("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})
    engine.closes["XMPW.MI"] = CLOSES
    run_job(db_engine, built([engine]), (), "RESOLVE", clock(NOW))
    with db_transaction(db_engine, write=False) as session:
        assert market_config.resolve_requested_at(session) == during


def test_a_failed_run_keeps_the_request(db_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_config(db_engine)
    with db_transaction(db_engine, write=True) as session:
        market_config.request_resolve(session, NOW - timedelta(hours=1))
    monkeypatch.setattr(market_jobs, "refresh_prices", lambda *a, **k: 1 / 0)
    run_job(db_engine, built(), (), "DAILY", clock(NOW))
    with db_transaction(db_engine, write=False) as session:
        assert market_config.resolve_requested_at(session) == NOW - timedelta(hours=1)


def test_the_re_check_corrects_an_inferred_class(db_engine: Engine) -> None:
    """Linked while no classifier knew it, the security got its class from Yahoo's unit; the
    classifier now says it is a bond: the class is corrected and the equity listing, which
    does not fit a bond, stops feeding it (manual prices until a bond engine has it)."""
    holdings(db_engine, ("alice", BOND_ISIN, "EUR"))
    equity = FakePrices("alpha", {BOND_ISIN: [listing("XMPB.MI", "MIL")]})
    equity.closes["XMPB.MI"] = CLOSES
    run_job(
        db_engine, built([equity], classify=[FakeClassifier("figi", {})]), (), "DAILY", clock(NOW)
    )
    figi = FakeClassifier("figi", {BOND_ISIN: facts(BOND_ISIN, PriceClass.BOND)})

    summary = run_job(
        db_engine, built([equity], classify=[figi]), (), "RESOLVE", clock(NOW + timedelta(days=1))
    )

    assert (summary.counts["reclassified"], summary.counts["deactivated"]) == (1, 1)
    with db_transaction(db_engine, write=False) as session:
        instrument = instruments.get_instrument(session, BOND_ISIN, "EUR")
        assert (instrument.price_class, instrument.class_source) == ("BOND", "figi")
        assert instruments.active_listing(session, instrument.id) is None
    assert len(equity.queries) == 1  # bonds are not asked of the equity engine


def test_the_re_check_keeps_a_listing_that_fits_the_confirmed_class(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    run_job(
        db_engine, built([alpha()], classify=[FakeClassifier("figi", {})]), (), "DAILY", clock(NOW)
    )
    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)})
    summary = run_job(db_engine, built([alpha()], classify=[figi]), (), "RESOLVE", clock(NOW))
    assert summary.counts["reclassified"] == 1 and "deactivated" not in summary.counts
    with db_transaction(db_engine, write=False) as session:
        instrument = instruments.get_instrument(session, ETF_ISIN, "EUR")
        assert instrument.class_source == "figi"
        assert instruments.active_listing(session, instrument.id) is not None


# --- the price status of each asset -----------------------------------------------------------


def statuses(engine: Engine, owner: str = "alice") -> dict[str, PriceStatus]:
    with db_transaction(engine, write=False) as session:
        user = session.scalars(select(models.User).where(models.User.username == owner)).one()
        owned = session.scalars(select(models.Asset).where(models.Asset.owner_id == user.id)).all()
        found = price_statuses(session, user.id, {a.id for a in owned})
        return {f"{a.isin}:{a.quote_currency}": found[a.id] for a in owned}


def test_price_status_through_the_life_of_a_listing(db_engine: Engine) -> None:
    holdings(
        db_engine,
        ("alice", ETF_ISIN, "EUR"),
        ("alice", None, "EUR"),
        ("alice", BOND_ISIN, "EUR"),
    )
    figi = FakeClassifier(
        "figi",
        {
            ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK),
            BOND_ISIN: facts(BOND_ISIN, PriceClass.BOND),
        },
    )
    engine = alpha()
    before = statuses(db_engine)
    assert before[f"{ETF_ISIN}:EUR"] == PriceStatus(PriceState.PENDING)  # not linked yet
    assert before["None:EUR"] == PriceStatus(PriceState.MANUAL)

    run_job(db_engine, built([engine], classify=[figi]), (), "DAILY", clock(NOW))
    after = statuses(db_engine)
    assert after[f"{ETF_ISIN}:EUR"] == PriceStatus(PriceState.OK)
    assert after[f"{BOND_ISIN}:EUR"] == PriceStatus(PriceState.NOT_AVAILABLE)  # no bond engine

    engine.closes["XMPW.MI"] = ProviderError(ErrorCode.NETWORK, "timeout")
    run_job(db_engine, built([engine], classify=[figi]), (), "DAILY", clock(NOW))
    assert statuses(db_engine)[f"{ETF_ISIN}:EUR"] == PriceStatus(PriceState.FAILING)

    engine.closes["XMPW.MI"] = CloseSeries("EUR", QuoteUnit.MINOR_CURRENCY_UNIT, ())
    run_job(db_engine, built([engine], classify=[figi]), (), "DAILY", clock(NOW))
    assert statuses(db_engine)[f"{ETF_ISIN}:EUR"] == PriceStatus(PriceState.MISMATCH, "UNIT")


def test_an_asset_that_does_not_fit_the_listing_has_its_own_mismatch(db_engine: Engine) -> None:
    """Bob records the ETF as a bond: his asset alone shows the unit mismatch (D-119)."""
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"), ("bob", ETF_ISIN, "EUR", "BOND"))
    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)})
    run_job(db_engine, built([alpha()], classify=[figi]), (), "DAILY", clock(NOW))
    assert statuses(db_engine)[f"{ETF_ISIN}:EUR"] == PriceStatus(PriceState.OK)
    assert statuses(db_engine, "bob")[f"{ETF_ISIN}:EUR"] == PriceStatus(PriceState.MISMATCH, "UNIT")


def test_an_instrument_waiting_for_a_classifier_is_pending(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    down = FakeClassifier("figi", {ETF_ISIN: ProviderError(ErrorCode.NETWORK, "timeout")})
    run_job(db_engine, built([alpha()], classify=[down]), (), "DAILY", clock(NOW))
    assert statuses(db_engine)[f"{ETF_ISIN}:EUR"] == PriceStatus(PriceState.PENDING)


def test_another_users_assets_get_no_status(db_engine: Engine) -> None:
    made = holdings(db_engine, ("alice", ETF_ISIN, "EUR"), ("bob", ETF_ISIN, "EUR"))
    with db_transaction(db_engine, write=False) as session:
        alice = session.scalars(select(models.User).where(models.User.username == "alice")).one()
        found = price_statuses(session, alice.id, {made[f"bob:{ETF_ISIN}:EUR"].id})
    assert found == {}
