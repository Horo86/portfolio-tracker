"""One market data job (D-121, D-132, D-134): the job log, the re-check request, the class
re-check, FX in the run, and each asset's price status. Fake engines and clock, a migrated
database file, invented ISINs, symbols, closes and rates."""

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest
from engines import (
    BOND_ISIN,
    EQUITY_UNITS,
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
    Capabilities,
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


def attempts(engine: Engine) -> int:
    with db_transaction(engine, write=False) as session:
        return session.get(models.MarketDataConfig, 1).resolve_attempts


def requested(engine: Engine):
    with db_transaction(engine, write=False) as session:
        return market_config.resolve_requested_at(session)


def listed_then_requested(engine: Engine) -> FakePrices:
    """Alice's ETF has its listing; then a re-check is requested."""
    holdings(engine, ("alice", ETF_ISIN, "EUR"))
    seed_config(engine)
    prices = alpha()
    run_job(engine, built([prices]), (), "DAILY", clock(NOW))
    with db_transaction(engine, write=True) as session:
        market_config.request_resolve(session, NOW + timedelta(hours=1))
    return prices


def test_a_re_check_that_postponed_an_instrument_keeps_the_request(db_engine: Engine) -> None:
    prices = listed_then_requested(db_engine)
    down = FakeClassifier("figi", {ETF_ISIN: ProviderError(ErrorCode.NETWORK, "timeout")})

    summary = run_job(
        db_engine, built([prices], classify=[down]), (), "DAILY", clock(NOW + timedelta(days=1))
    )

    assert (summary.counts["postponed"], summary.counts["recheck_pending"]) == (1, 1)
    assert runs(db_engine)[-1].counts == summary.counts
    assert (requested(db_engine), attempts(db_engine)) == (NOW + timedelta(hours=1), 1)
    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)})
    summary = run_job(
        db_engine, built([prices], classify=[figi]), (), "DAILY", clock(NOW + timedelta(days=2))
    )
    assert summary.recheck and "recheck_pending" not in summary.counts
    assert (requested(db_engine), attempts(db_engine)) == (None, 0)


def test_a_re_check_whose_search_failed_keeps_the_request(db_engine: Engine) -> None:
    prices = listed_then_requested(db_engine)
    prices.answers[ETF_ISIN] = ProviderError(ErrorCode.HTTP_ERROR, "503")
    summary = run_job(db_engine, built([prices]), (), "DAILY", clock(NOW + timedelta(days=1)))
    assert summary.counts["recheck_pending"] == 1 and attempts(db_engine) == 1
    assert requested(db_engine) is not None


def test_a_re_check_that_never_completes_is_abandoned_after_seven_runs(
    db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    assert market_jobs.RECHECK_MAX_RUNS == 7
    prices = listed_then_requested(db_engine)
    prices.answers[ETF_ISIN] = ProviderError(ErrorCode.HTTP_ERROR, "503")
    engines = built([prices])
    for day in range(1, 7):
        summary = run_job(db_engine, engines, (), "DAILY", clock(NOW + timedelta(days=day)))
        assert summary.counts["recheck_pending"] == 1 and attempts(db_engine) == day

    with caplog.at_level(logging.WARNING):
        summary = run_job(db_engine, engines, (), "DAILY", clock(NOW + timedelta(days=7)))

    stored = runs(db_engine)[-1].counts
    assert stored["recheck_abandoned"] == 1 and "recheck_pending" not in stored
    assert (requested(db_engine), attempts(db_engine)) == (None, 0)
    assert "listing re-check abandoned" in caplog.text
    queries = len(prices.queries)
    run_job(db_engine, engines, (), "DAILY", clock(NOW + timedelta(days=8)))
    assert len(prices.queries) == queries  # a daily run again: the listing is not searched


def test_a_request_made_during_an_unfinished_re_check_starts_afresh(db_engine: Engine) -> None:
    """The new request was stamped by a clock behind the app's: it is still told apart from
    the one the run read, and stays pending without the run's attempt."""
    prices = listed_then_requested(db_engine)
    behind = NOW - timedelta(days=1)

    class RequestsWhileSearching(FakePrices):
        def search(self, query, *, max_wait=None):
            with db_transaction(db_engine, write=True) as session:
                market_config.request_resolve(session, behind)
            raise ProviderError(ErrorCode.HTTP_ERROR, "503")

    failing = RequestsWhileSearching("alpha", {})
    failing.closes = prices.closes
    summary = run_job(db_engine, built([failing]), (), "DAILY", clock(NOW + timedelta(days=1)))
    assert "recheck_pending" not in summary.counts
    assert (requested(db_engine), attempts(db_engine)) == (behind, 0)


def test_a_completed_re_check_leaves_a_request_stamped_behind_its_start(
    db_engine: Engine,
) -> None:
    prices = listed_then_requested(db_engine)
    behind = NOW - timedelta(days=1)

    class RequestsWhileSearching(FakePrices):
        def search(self, query, *, max_wait=None):
            with db_transaction(db_engine, write=True) as session:
                market_config.request_resolve(session, behind)
            return super().search(query, max_wait=max_wait)

    again = RequestsWhileSearching("alpha", prices.answers)
    again.closes = prices.closes
    run_job(db_engine, built([again]), (), "DAILY", clock(NOW + timedelta(days=1)))
    assert requested(db_engine) == behind


def test_class_re_check_counts_survive_a_run_that_stops_half_way(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two inferred classes; the run fails while correcting the second: the first one's count
    is in the job log."""
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"), ("alice", BOND_ISIN, "EUR"))
    equity = FakePrices(
        "alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")], BOND_ISIN: [listing("XMPB.MI", "MIL")]}
    )
    equity.closes |= {"XMPW.MI": CLOSES, "XMPB.MI": CLOSES}
    run_job(
        db_engine, built([equity], classify=[FakeClassifier("figi", {})]), (), "DAILY", clock(NOW)
    )
    figi = FakeClassifier(
        "figi",
        {
            ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK),
            BOND_ISIN: facts(BOND_ISIN, PriceClass.BOND),
        },
    )
    real = instruments.set_price_class
    calls = []

    def breaks_the_second_time(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(instruments, "set_price_class", breaks_the_second_time)
    summary = run_job(db_engine, built([equity], classify=[figi]), (), "RESOLVE", clock(NOW))

    assert summary.result == "FAILED"
    assert runs(db_engine)[-1].counts == {"confirmed": 1}


def test_a_warning_after_seven_runs_in_a_row_with_postponed_instruments(
    db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    assert market_jobs.POSTPONED_WARNING_RUNS == 7
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    down = FakeClassifier("figi", {ETF_ISIN: ProviderError(ErrorCode.NETWORK, "timeout")})
    engines = built([alpha()], classify=[down])
    for day in range(6):
        with caplog.at_level(logging.WARNING):
            run_job(db_engine, engines, (), "DAILY", clock(NOW + timedelta(days=day)))
    assert "instruments postponed in every recent run" not in caplog.text

    with caplog.at_level(logging.WARNING):
        run_job(db_engine, engines, (), "DAILY", clock(NOW + timedelta(days=6)))
    (record,) = [r for r in caplog.records if "postponed in every" in r.message]
    assert (record.runs, record.postponed) == (7, 1)
    assert ETF_ISIN not in caplog.text + repr(record.__dict__)


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

    assert (
        runs(db_engine)[-1].counts
        == summary.counts
        == {
            "reclassified": 1,
            "deactivated": 1,
            "no_engine": 1,  # no bond engine
        }
    )
    assert figi.asked == [BOND_ISIN]  # once: the walk reuses the re-check's answer
    with db_transaction(db_engine, write=False) as session:
        instrument = instruments.get_instrument(session, BOND_ISIN, "EUR")
        assert (instrument.price_class, instrument.class_source) == ("BOND", "figi")
        assert instruments.active_listing(session, instrument.id) is None
    assert len(equity.queries) == 1  # bonds are not asked of the equity engine


def test_a_listing_switched_off_by_a_class_correction_is_never_offered_again(
    db_engine: Engine,
) -> None:
    """Alpha serves both classes. Its equity listing fed the security until the classifier
    said bond: the listing goes with its closes, alpha's check is a unit mismatch, and a
    failing search of alpha afterwards does not bring the listing back."""
    holdings(db_engine, ("alice", BOND_ISIN, "EUR"))
    both = Capabilities(
        roles=frozenset({Role.ETF_STOCK, Role.BOND}),
        search_isin=True,
        units=EQUITY_UNITS | {QuoteUnit.PERCENT_OF_NOMINAL},
    )
    alpha = FakePrices("alpha", {BOND_ISIN: [listing("XMPB.MI", "MIL")]}, both)
    alpha.closes["XMPB.MI"] = CLOSES
    engines = BuiltEngines(
        {Role.ETF_STOCK: (alpha,), Role.BOND: (alpha,), Role.FX: (), Role.CLASSIFY: ()}
    )
    run_job(db_engine, engines, (), "DAILY", clock(NOW))
    figi = FakeClassifier("figi", {BOND_ISIN: facts(BOND_ISIN, PriceClass.BOND)})
    engines = BuiltEngines({**engines.by_role, Role.CLASSIFY: (figi,)})
    alpha.answers[BOND_ISIN] = ProviderError(ErrorCode.NETWORK, "timeout")

    summary = run_job(db_engine, engines, (), "RESOLVE", clock(NOW + timedelta(days=1)))

    assert summary.counts["deactivated"] == 1
    with db_transaction(db_engine, write=False) as session:
        instrument = instruments.get_instrument(session, BOND_ISIN, "EUR")
        assert instruments.listings(session, instrument.id) == []
        assert session.scalars(select(models.InstrumentPrice)).all() == []
        (check,) = instruments.checks(session, instrument.id)
        assert (check.status, check.symbol) == ("FAILING", None)

    alpha.answers[BOND_ISIN] = [listing("XMPB.MI", "MIL")]
    run_job(db_engine, engines, (), "DAILY", clock(NOW + timedelta(days=2)))
    with db_transaction(db_engine, write=False) as session:
        instrument = instruments.get_instrument(session, BOND_ISIN, "EUR")
        assert instruments.active_listing(session, instrument.id) is None
        (check,) = instruments.checks(session, instrument.id)
        assert (check.status, check.mismatch) == ("MISMATCH", "UNIT")  # judged as a bond


def test_the_re_check_keeps_a_listing_that_fits_the_confirmed_class(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    run_job(
        db_engine, built([alpha()], classify=[FakeClassifier("figi", {})]), (), "DAILY", clock(NOW)
    )
    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)})
    summary = run_job(db_engine, built([alpha()], classify=[figi]), (), "RESOLVE", clock(NOW))
    stored = runs(db_engine)[-1].counts
    assert stored == summary.counts and stored["confirmed"] == 1  # the class did not change
    assert "reclassified" not in stored and "deactivated" not in stored
    assert figi.asked == [ETF_ISIN]
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
