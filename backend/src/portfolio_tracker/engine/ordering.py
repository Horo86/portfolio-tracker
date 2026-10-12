"""Deterministic replay order of an account's transactions (domain-rules §4, D-45)."""

from collections.abc import Iterable
from datetime import date, time
from uuid import UUID

from portfolio_tracker.engine.model import Transaction, TransactionType

T = TransactionType

# Same-day priority of transactions without trade_time.
_TYPE_PRIORITY = {
    T.DEPOSIT: 0,
    T.STOCK_SPLIT: 1,
    T.BUY: 2,
    T.DIVIDEND: 3,
    T.COUPON: 3,
    T.SELL: 4,
    T.WITHDRAWAL: 5,
}


def replay_key(tx: Transaction) -> tuple[date, int, int, time, int, UUID]:
    """By date; untimed first by type priority; then timed by time; then insertion sequence.

    The id is a last resort so the order never depends on the input order, even if two
    transactions share a sequence (the app keeps sequences unique per account, D-70).
    """
    if tx.trade_time is None:
        return (tx.trade_date, 0, _TYPE_PRIORITY[tx.type], time.min, tx.sequence, tx.id)
    return (tx.trade_date, 1, 0, tx.trade_time, tx.sequence, tx.id)


def replay_order(transactions: Iterable[Transaction]) -> list[Transaction]:
    return sorted(transactions, key=replay_key)
