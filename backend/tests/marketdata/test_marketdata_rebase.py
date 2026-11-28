"""One uniform series per listing (D-201): the comparison of the last stored days, the full
download when a series was rescaled or marked, its refusals and the daily update that still
happens when it fails. Fake engines, a migrated database file, invented ISINs and closes."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from engines import ETF_ISIN, NOW, FakePrices, built, holdings, listing
from helpers import fake_isin
from sqlalchemy import Engine, select

from portfolio_tracker.db import db_transaction, models
from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.base import Close, CloseSeries, ErrorCode, ProviderError
from portfolio_tracker.repositories import accounts, instruments, transactions
from portfolio_tracker.services import reports
from portfolio_tracker.services.fx_rates import horizon_start
from portfolio_tracker.services.market_data import (
    REBASE_TOLERANCE,
    REBASE_WINDOW,
    REFETCH_ACCEPT_AFTER_DAYS,
    REFETCH_SHORT_START_DAYS,
    rebase_needed,
    refetch_refusal,
    refresh_prices,
)

TODAY = NOW.date()  # Friday 6 March 2026
SYMBOL = "XMPW.MI"


def weekdays(first: date, last: date) -> list[date]:
    days, day = [], first
    while day <= last:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


# 40 trading days up to Friday 27 February, stored by the first run (made on that day).
DAYS = weekdays(date(2026, 1, 5), date(2026, 2, 27))
NEW = weekdays(date(2026, 3, 2), TODAY)  # the week after: Monday 2 to Friday 6 March


def level(day: date) -> Decimal:
    """An invented close for each day: 100.00, 100.10, … (never twice the same)."""
    return Decimal("100.00") + Decimal("0.10") * (day - DAYS[0]).days


def answer(days, scale: Decimal = Decimal(1), *, scaled_before: date | None = None):
    """Closes on `days`; with `scaled_before`, the days before it multiplied by `scale` (a
    provider rescaling history before an ex-date), otherwise every day."""
    closes = tuple(
        Close(day, level(day) * (scale if scaled_before is None or day < scaled_before else 1))
        for day in days
    )
    return CloseSeries("EUR", QuoteUnit.CURRENCY, closes)


class Scripted(FakePrices):
    """A fake engine whose answers come one per call, in order (an exception is raised)."""

    def __init__(self) -> None:
        super().__init__("alpha", {ETF_ISIN: [listing(SYMBOL, "MIL")]})
        self.script: list[object] = []

    def daily_closes(self, symbol: str, start: date, end: date) -> CloseSeries:
        self.fetches.append((symbol, start, end))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, CloseSeries)
        return item


@pytest.fixture
def alpha(db_engine: Engine) -> Iterator[Scripted]:
    """Alice holds the security; a first run on 27 February stored `DAYS`."""
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    engine = Scripted()
    engine.script.append(answer(DAYS))
    run(db_engine, engine, datetime(2026, 2, 27, 18, tzinfo=UTC))
    engine.fetches.clear()
    yield engine
    assert engine.script == [], "an answer was left unused"


def run(engine: Engine, alpha: FakePrices, when: datetime = NOW, *, engines=None):
    return refresh_prices(engine, engines or built([alpha]), (), when)[1]


def the_listing(engine: Engine) -> models.InstrumentListing:
    with db_transaction(engine, write=False) as session:
        return session.scalars(select(models.InstrumentListing)).one()


def stored(engine: Engine) -> dict[date, Decimal]:
    with db_transaction(engine, write=False) as session:
        listing_id = session.scalars(select(models.InstrumentListing.id)).one()
        return {c.day: c.close for c in instruments.stored_closes(session, listing_id)}


def mark(engine: Engine, at: datetime) -> None:
    with db_transaction(engine, write=True) as session:
        session.scalars(select(models.InstrumentListing)).one().refetch_requested_at = at


def store(engine: Engine, closes: dict[date, Decimal]) -> None:
    with db_transaction(engine, write=True) as session:
        listing_id = session.scalars(select(models.InstrumentListing.id)).one()
        instruments.store_closes(
            session, listing_id, [instruments.StoredClose(d, c) for d, c in closes.items()]
        )


def window_start() -> date:
    return DAYS[-REBASE_WINDOW]


# --- the comparison alone ----------------------------------------------------------------------

D = [date(2026, 3, d) for d in (2, 3, 4, 5, 6)]  # a week of stored days; D[4] the newest


def closes(*values: str | None) -> dict[date, Decimal]:
    return {day: Decimal(v) for day, v in zip(D, values, strict=False) if v is not None}


@pytest.mark.parametrize(
    ("stored_days", "answer_days", "new_day", "rebase"),
    [
        (closes("10", "10", "10", "10", "10"), closes("10", "10", "10", "10", "10"), False, False),
        # A correction within 1 % is no rescale; exactly 1 % neither.
        (
            closes("10", "10", "10", "10", "10"),
            closes("10", "10.1", "9.95", "10", "10"),
            False,
            False,
        ),
        (
            closes("10", "10", "10", "10", "10"),
            closes("10", "10.11", "10", "10", "10"),
            False,
            True,
        ),
        # Every day rescaled (a 4:1 split applied to the history).
        (
            closes("10", "10", "10", "10", "10"),
            closes("2.5", "2.5", "2.5", "2.5", "2.5"),
            False,
            True,
        ),
        # The window straddles the ex-date: two days agree, two do not.
        (closes("10", "10", "5", "5", "5"), closes("5", "5", "5", "5", "5"), False, True),
        # Only the newest stored day differs (a partial bar): no rebase.
        (closes("10", "10", "10", "10", "10"), closes("10", "10", "10", "10", "14"), True, False),
        # A null day on either side is not comparable; one agreeing day is enough.
        (closes("10", None, None, None, "10"), closes(None, "7", "7", "7", "10"), True, True),
        (closes("10", None, None, None, "10"), closes("10", "7", "7", "7", "10"), True, False),
        (closes("10", "10", "10", "10", "10"), closes("10", None, None, None, None), True, False),
        # No comparable day: a rebase only if the answer brings new days.
        (closes(None, None, None, None, "10"), closes("10", "10", "10", "10", "10"), True, True),
        (closes(None, None, None, None, "10"), closes("10", "10", "10", "10", "10"), False, False),
    ],
)
def test_the_comparison_of_the_last_stored_days(
    stored_days: dict[date, Decimal],
    answer_days: dict[date, Decimal],
    new_day: bool,
    rebase: bool,
) -> None:
    if new_day:
        answer_days = {**answer_days, date(2026, 3, 9): Decimal("10")}
    assert rebase_needed(stored_days, answer_days, D[4]) is rebase


def test_the_shipped_window_and_tolerance() -> None:
    assert (15, Decimal("0.01")) == (REBASE_WINDOW, REBASE_TOLERANCE)
    assert (REFETCH_SHORT_START_DAYS, REFETCH_ACCEPT_AFTER_DAYS) == (31, 7)


FIRST, LAST = date(2006, 3, 6), date(2026, 3, 6)


@pytest.mark.parametrize(
    ("first", "last", "age_days", "refusal"),
    [
        (FIRST, LAST, 0, None),
        (None, None, 0, "EMPTY"),
        (FIRST, LAST - timedelta(days=5), 0, None),  # 5 days earlier: accepted
        (FIRST, LAST - timedelta(days=6), 0, "STALE_END"),
        (FIRST + timedelta(days=31), LAST, 0, None),
        (FIRST + timedelta(days=32), LAST, 0, "SHORT_START"),
        (FIRST + timedelta(days=32), LAST, 7, "SHORT_START"),
        (FIRST + timedelta(days=32), LAST, 8, None),  # an old mark accepts a shorter history
        (FIRST + timedelta(days=32), LAST - timedelta(days=6), 30, "STALE_END"),
    ],
)
def test_the_refusals_of_a_full_answer(first, last, age_days: int, refusal: str | None) -> None:
    marked = NOW - timedelta(days=age_days)
    assert refetch_refusal(first, last, FIRST, LAST, marked, NOW) == refusal


def test_nothing_stored_refuses_only_an_empty_answer() -> None:
    assert refetch_refusal(None, None, None, None, NOW, NOW) == "EMPTY"
    assert refetch_refusal(LAST, LAST, None, None, NOW, NOW) is None


# --- the daily update with the comparison -----------------------------------------------------


def test_an_unchanged_overlap_is_appended_from_the_fifteenth_last_stored_day(
    db_engine: Engine, alpha: Scripted
) -> None:
    alpha.script.append(answer(DAYS[-REBASE_WINDOW:] + NEW))

    result = run(db_engine, alpha)

    assert alpha.fetches == [(SYMBOL, window_start(), TODAY)]
    assert (result.rebased, result.refetched, result.stored) == (0, 0, REBASE_WINDOW + 5)
    assert stored(db_engine) == {day: level(day) for day in DAYS + NEW}


def test_a_rescaled_series_is_not_appended_but_downloaded_again_in_the_same_run(
    db_engine: Engine, alpha: Scripted
) -> None:
    quarter = Decimal("0.25")
    alpha.script += [answer(DAYS[-REBASE_WINDOW:] + NEW, quarter), answer(DAYS + NEW, quarter)]

    result = run(db_engine, alpha)

    assert alpha.fetches == [
        (SYMBOL, window_start(), TODAY),
        (SYMBOL, horizon_start(TODAY), TODAY),  # the earlier of the first day and the horizon
    ]
    assert (result.rebased, result.refetched, result.updated) == (1, 1, 1)
    assert stored(db_engine) == {day: level(day) * quarter for day in DAYS + NEW}
    row = the_listing(db_engine)
    assert row.refetch_requested_at is None and row.status == "OK"


def test_a_window_straddling_the_ex_date_is_rebased(db_engine: Engine, alpha: Scripted) -> None:
    ex_date = DAYS[-3]  # it and the next day agree (the newest is not compared), the rest not
    half = Decimal("0.5")
    alpha.script += [
        answer(DAYS[-REBASE_WINDOW:] + NEW, half, scaled_before=ex_date),
        answer(DAYS + NEW, half, scaled_before=ex_date),
    ]

    result = run(db_engine, alpha)

    assert (result.rebased, result.refetched) == (1, 1)
    assert stored(db_engine)[DAYS[0]] == level(DAYS[0]) * half
    assert stored(db_engine)[ex_date] == level(ex_date)


def test_only_the_newest_stored_day_differing_is_no_rebase(
    db_engine: Engine, alpha: Scripted
) -> None:
    changed = answer(DAYS[-REBASE_WINDOW:])
    changed = CloseSeries(
        "EUR", QuoteUnit.CURRENCY, (*changed.closes[:-1], Close(DAYS[-1], Decimal("1")))
    )
    alpha.script.append(changed)

    result = run(db_engine, alpha)

    assert (result.rebased, len(alpha.fetches)) == (0, 1)
    assert stored(db_engine)[DAYS[-1]] == Decimal("1")  # the partial bar is replaced


def test_new_days_without_any_comparable_day_are_rebased(
    db_engine: Engine, alpha: Scripted
) -> None:
    alpha.script += [answer(NEW), answer(DAYS + NEW)]  # the stored days come back null

    result = run(db_engine, alpha)

    assert (result.rebased, result.refetched, len(alpha.fetches)) == (1, 1, 2)


def test_a_late_rescale_is_caught_on_the_run_after_it(db_engine: Engine, alpha: Scripted) -> None:
    """The lag: the split's first days arrive at the new level while the history is not yet
    rescaled (an unchanged run, the series mixed for a day), then the history is rescaled."""
    half, ex_date = Decimal("0.5"), NEW[0]
    mixed = CloseSeries(
        "EUR",
        QuoteUnit.CURRENCY,
        tuple(
            Close(d, level(d) * (half if d >= ex_date else 1))
            for d in DAYS[-REBASE_WINDOW:] + NEW[:2]
        ),
    )
    alpha.script.append(mixed)
    first = run(db_engine, alpha, datetime(2026, 3, 3, 18, tzinfo=UTC))
    assert (first.rebased, first.refetched) == (0, 0)  # nothing to compare differs yet

    rescaled = answer(DAYS + NEW, half)
    alpha.script += [rescaled, rescaled]
    second = run(db_engine, alpha)

    assert (second.rebased, second.refetched) == (1, 1)
    assert stored(db_engine) == {day: level(day) * half for day in DAYS + NEW}


# --- the full download of a marked listing --------------------------------------------------


def test_a_marked_listing_is_downloaded_whole_and_its_mark_cleared(
    db_engine: Engine, alpha: Scripted
) -> None:
    mark(db_engine, NOW - timedelta(days=1))
    alpha.script.append(answer(DAYS + NEW, Decimal("0.5"), scaled_before=DAYS[10]))

    result = run(db_engine, alpha)

    assert alpha.fetches == [(SYMBOL, horizon_start(TODAY), TODAY)]  # one call
    assert (result.refetched, result.rebased, result.refetch_refused) == (1, 0, 0)
    assert stored(db_engine)[DAYS[0]] == level(DAYS[0]) / 2
    assert the_listing(db_engine).refetch_requested_at is None


def test_a_mark_keeps_the_stored_days_after_the_answers_last_day(
    db_engine: Engine, alpha: Scripted
) -> None:
    mark(db_engine, NOW - timedelta(days=1))
    alpha.script.append(answer(DAYS[:-2]))  # ends 2 trading days before the stored series

    result = run(db_engine, alpha)

    assert result.refetched == 1
    assert stored(db_engine) == {day: level(day) for day in DAYS}  # the last close never goes back


def test_a_rebase_replaces_every_day_even_after_the_answers_last_day(
    db_engine: Engine, alpha: Scripted
) -> None:
    half = Decimal("0.5")
    alpha.script += [answer(DAYS[-REBASE_WINDOW:], half), answer(DAYS[:-2], half)]

    result = run(db_engine, alpha, datetime(2026, 2, 28, 18, tzinfo=UTC))

    assert (result.rebased, result.refetched) == (1, 1)
    assert stored(db_engine) == {day: level(day) * half for day in DAYS[:-2]}


def test_a_mark_whose_answer_differs_from_the_stored_days_replaces_every_day(
    db_engine: Engine, alpha: Scripted
) -> None:
    """A mark left by an earlier run's refused rebase looks like the upgrade's: the full answer
    itself shows the rescale, so no stored day at the old scale is kept."""
    half = Decimal("0.5")
    mark(db_engine, NOW - timedelta(days=1))
    alpha.script.append(answer(DAYS[:-2], half))

    result = run(db_engine, alpha, datetime(2026, 2, 28, 18, tzinfo=UTC))

    assert result.refetched == 1
    assert stored(db_engine) == {day: level(day) * half for day in DAYS[:-2]}


@pytest.mark.parametrize(
    ("full", "reason"),
    [
        (CloseSeries("EUR", QuoteUnit.CURRENCY, ()), "EMPTY"),
        (answer(DAYS[25:] + NEW), "SHORT_START"),  # starts more than 31 days later
        (answer(DAYS[:20]), "STALE_END"),
    ],
)
def test_a_refused_download_still_does_the_daily_update_and_keeps_the_mark(
    db_engine: Engine, alpha: Scripted, full: CloseSeries, reason: str, caplog
) -> None:
    marked = NOW - timedelta(days=1)
    mark(db_engine, marked)
    alpha.script += [full, answer(DAYS[-REBASE_WINDOW:] + NEW)]

    result = run(db_engine, alpha)

    assert alpha.fetches == [
        (SYMBOL, horizon_start(TODAY), TODAY),
        (SYMBOL, window_start(), TODAY),  # the daily update, not a second full download
    ]
    assert (result.refetch_refused, result.refetched, result.updated) == (1, 0, 1)
    assert stored(db_engine) == {day: level(day) for day in DAYS + NEW}  # today's close stored
    assert the_listing(db_engine).refetch_requested_at == marked  # kept, never moved
    assert any(getattr(r, "reason", None) == reason for r in caplog.records)


def test_a_failed_download_still_does_the_daily_update_and_keeps_the_mark(
    db_engine: Engine, alpha: Scripted
) -> None:
    marked = NOW - timedelta(days=1)
    mark(db_engine, marked)
    alpha.script += [
        ProviderError(ErrorCode.NOT_DAILY, "answer not daily"),
        answer(DAYS[-REBASE_WINDOW:] + NEW),
    ]

    result = run(db_engine, alpha)

    assert result.errors == {"NOT_DAILY": 1} and result.refetched == 0
    assert stored(db_engine) == {day: level(day) for day in DAYS + NEW}
    row = the_listing(db_engine)
    assert (row.refetch_requested_at, row.status) == (marked, "OK")


def test_a_failed_download_and_a_rescaled_update_store_nothing(
    db_engine: Engine, alpha: Scripted
) -> None:
    marked = NOW - timedelta(days=1)
    mark(db_engine, marked)
    alpha.script += [
        ProviderError(ErrorCode.NETWORK, "down"),
        answer(DAYS[-REBASE_WINDOW:] + NEW, Decimal("0.5")),
    ]

    result = run(db_engine, alpha)

    assert len(alpha.fetches) == 2 and result.rebased == 0  # no second full download
    assert stored(db_engine) == {day: level(day) for day in DAYS}
    assert the_listing(db_engine).refetch_requested_at == marked


def test_a_refused_rebase_keeps_the_mark_for_the_next_run(
    db_engine: Engine, alpha: Scripted
) -> None:
    half = Decimal("0.5")
    alpha.script += [answer(DAYS[-REBASE_WINDOW:] + NEW, half), answer(DAYS[25:] + NEW, half)]

    first = run(db_engine, alpha)

    assert (first.rebased, first.refetch_refused, len(alpha.fetches)) == (1, 1, 2)
    assert stored(db_engine) == {day: level(day) for day in DAYS}  # nothing mixed stored
    assert the_listing(db_engine).refetch_requested_at == NOW

    alpha.script.append(answer(DAYS + NEW, half))
    second = run(db_engine, alpha, NOW + timedelta(days=1))

    assert (second.refetched, second.rebased) == (1, 0)
    assert stored(db_engine) == {day: level(day) * half for day in DAYS + NEW}


def test_a_short_history_is_accepted_once_the_mark_is_old_enough(
    db_engine: Engine, alpha: Scripted
) -> None:
    mark(db_engine, NOW - timedelta(days=REFETCH_ACCEPT_AFTER_DAYS + 1))
    alpha.script.append(answer(DAYS[25:] + NEW))  # starts 5 weeks after the stored series

    result = run(db_engine, alpha)

    assert (result.refetched, result.shortened, result.refetch_refused) == (1, 1, 0)
    assert min(stored(db_engine)) == DAYS[25]  # the older days cannot be put on today's scale


def test_a_download_in_another_unit_keeps_the_mark_and_clears_with_the_next_answer(
    db_engine: Engine, alpha: Scripted
) -> None:
    marked = NOW - timedelta(days=1)
    mark(db_engine, marked)
    alpha.script.append(CloseSeries("USD", QuoteUnit.CURRENCY, answer(DAYS).closes))

    result = run(db_engine, alpha)

    assert result.mismatch == 1 and len(alpha.fetches) == 1  # nothing to fall back to
    row = the_listing(db_engine)
    assert (row.status, row.mismatch, row.refetch_requested_at) == ("MISMATCH", "CURRENCY", marked)
    assert stored(db_engine) == {day: level(day) for day in DAYS}

    alpha.script.append(answer(DAYS + NEW))
    later = run(db_engine, alpha, NOW + timedelta(days=1))

    row = the_listing(db_engine)
    assert later.refetched == 1 and (row.status, row.refetch_requested_at) == ("OK", None)


def test_a_disabled_engines_marked_listing_is_not_called_and_stays_marked(
    db_engine: Engine, alpha: Scripted
) -> None:
    marked = NOW - timedelta(days=1)
    mark(db_engine, marked)

    run(db_engine, alpha, engines=built([]))

    assert alpha.fetches == [] and the_listing(db_engine).refetch_requested_at == marked
    assert stored(db_engine) == {day: level(day) for day in DAYS}


def test_marked_listings_are_downloaded_oldest_mark_first(db_engine: Engine) -> None:
    other = fake_isin(53)
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"), ("alice", other, "EUR"))
    alpha = FakePrices(
        "alpha", {ETF_ISIN: [listing(SYMBOL, "MIL")], other: [listing("ZZB.MI", "MIL")]}
    )
    alpha.closes[SYMBOL] = alpha.closes["ZZB.MI"] = answer(DAYS)
    run(db_engine, alpha, datetime(2026, 2, 27, 18, tzinfo=UTC))
    with db_transaction(db_engine, write=True) as session:
        for row in session.scalars(select(models.InstrumentListing)):
            row.refetch_requested_at = NOW - timedelta(days=2 if row.symbol == "ZZB.MI" else 1)
    alpha.fetches.clear()

    run(db_engine, alpha)

    assert [symbol for symbol, _, _ in alpha.fetches] == ["ZZB.MI", SYMBOL]


# --- current figures --------------------------------------------------------------------------


def test_a_download_that_changes_only_past_closes_leaves_the_summary_unchanged(
    db_engine: Engine, alpha: Scripted
) -> None:
    with db_transaction(db_engine, write=True) as session:
        alice = session.scalars(select(models.User)).one()
        account = accounts.add_account(session, alice.id, name="Broker", currency="EUR")
        asset = session.scalars(select(models.Asset)).one()
        for kind, values in (
            ("DEPOSIT", {"total_amount": Decimal("5000")}),
            (
                "BUY",
                {
                    "asset_id": asset.id,
                    "quantity": Decimal("10"),
                    "unit_price": Decimal("100"),
                    "gross_amount": Decimal("1000"),
                    "fees": Decimal("0"),
                    "total_amount": Decimal("-1000"),
                },
            ),
        ):
            session.add(
                models.Transaction(
                    account_id=account.id,
                    type=kind,
                    trade_date=DAYS[0],
                    sequence=transactions.next_sequence(session, account.id),
                    **values,
                )
            )
    alpha.script.append(answer(DAYS[-REBASE_WINDOW:] + NEW))
    run(db_engine, alpha)
    before = summary(db_engine)
    assert before.figures.securities_value == 10 * level(TODAY)  # valued at today's close

    mark(db_engine, NOW)
    alpha.script.append(answer(DAYS + NEW, Decimal("0.5"), scaled_before=NEW[-1]))
    run(db_engine, alpha, NOW + timedelta(hours=1))

    assert stored(db_engine)[DAYS[0]] == level(DAYS[0]) / 2  # the past did change
    assert summary(db_engine) == before


def summary(engine: Engine):
    with db_transaction(engine, write=False) as session:
        user = session.scalars(select(models.User)).one()
        return reports.portfolio_report(session, user, TODAY)
