import random
from datetime import date, time

from factories import buy, deposit, dividend, etf, sell, split, withdrawal

from portfolio_tracker.engine.ordering import replay_order

ETF = etf()
DAY = date(2026, 5, 4)


def test_untimed_same_day_follow_type_priority() -> None:
    w = withdrawal(DAY, "1")
    s = sell(ETF, DAY, "1", "10")
    div = dividend(ETF, DAY, "1")
    b = buy(ETF, DAY, "1", "10")
    sp = split(ETF, DAY, "2", "1")
    dep = deposit(DAY, "100")

    assert replay_order([w, s, div, b, sp, dep]) == [dep, sp, b, div, s, w]


def test_timed_come_after_untimed_and_sort_by_time() -> None:
    late = buy(ETF, DAY, "1", "10", at=time(15, 0))
    early = sell(ETF, DAY, "1", "10", at=time(9, 30))
    untimed_withdrawal = withdrawal(DAY, "1")

    assert replay_order([late, early, untimed_withdrawal]) == [untimed_withdrawal, early, late]


def test_ties_use_insertion_sequence() -> None:
    first = buy(ETF, DAY, "1", "10", sequence=10)
    second = buy(ETF, DAY, "2", "20", sequence=11)
    timed_a = sell(ETF, DAY, "1", "10", at=time(10), sequence=20)
    timed_b = buy(ETF, DAY, "1", "10", at=time(10), sequence=21)

    assert replay_order([timed_b, second, timed_a, first]) == [first, second, timed_a, timed_b]


def test_dates_come_first() -> None:
    tomorrow_deposit = deposit(date(2026, 5, 5), "1")
    today_withdrawal = withdrawal(DAY, "1", at=time(23, 59))

    assert replay_order([tomorrow_deposit, today_withdrawal]) == [
        today_withdrawal,
        tomorrow_deposit,
    ]


def test_e7_untimed_sell_is_replayed_before_timed_buy() -> None:
    timed_buy = buy(ETF, DAY, "10", "1000", at=time(10, 0))
    untimed_sell = sell(ETF, DAY, "10", "1000")

    assert replay_order([timed_buy, untimed_sell]) == [untimed_sell, timed_buy]


def test_order_does_not_depend_on_input_order() -> None:
    txs = [
        deposit(DAY, "100"),
        buy(ETF, DAY, "1", "10"),
        buy(ETF, DAY, "1", "10", at=time(11)),
        sell(ETF, DAY, "1", "10", at=time(11)),
        dividend(ETF, date(2026, 6, 1), "1"),
    ]
    expected = replay_order(txs)
    rng = random.Random(7)
    for _ in range(20):
        shuffled = txs[:]
        rng.shuffle(shuffled)
        assert replay_order(shuffled) == expected
