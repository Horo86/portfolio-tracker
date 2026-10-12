"""Property tests for ordering, conservation rules and edits (ADR-0010, D-54). Invented data.

Generated ledgers mix ETFs and a bond, several transactions per day, timed and untimed ones,
signed taxes, accrued interest and `affects_cash = false`. Every generated ledger is valid.
"""

import dataclasses
import random
from datetime import date, time, timedelta
from decimal import Decimal

from factories import (
    assets_by_id,
    bond,
    buy,
    coupon,
    deposit,
    dividend,
    etf,
    sell,
    split,
    withdrawal,
)
from hypothesis import given, reject, settings
from hypothesis import strategies as st

from portfolio_tracker.engine.decimals import or_zero
from portfolio_tracker.engine.edits import bulk_edit, create, delete_all_for_asset, edit
from portfolio_tracker.engine.ledger import Ledger, QuantityConflictError, replay
from portfolio_tracker.engine.model import Asset, Transaction, TransactionType
from portfolio_tracker.engine.validation import validate_transaction

T = TransactionType
Generated = tuple[list[Asset], list[Transaction]]

START = date(2020, 1, 1)
QUANTITIES = st.decimals(min_value="0.00000001", max_value="1000", places=8)
AMOUNTS = st.decimals(min_value="0", max_value="100000", places=2)
POSITIVE = st.decimals(min_value="0.01", max_value="100000", places=2)
SMALL = st.decimals(min_value="0", max_value="20", places=2)
SIGNED = st.decimals(min_value="-20", max_value="20", places=2)
TIMES = st.one_of(st.none(), st.sampled_from([time(9), time(12, 30), time(17, 45)]))
KINDS = ["buy", "buy", "sell", "sell", "split", "income", "deposit", "withdrawal"]

# Replays inside the generator make some examples slow; timing is not under test.
SLOW = settings(deadline=None, max_examples=60)


def _maybe(draw: st.DrawFn, strategy: st.SearchStrategy[Decimal]) -> str | None:
    value = draw(st.one_of(st.none(), strategy))
    return None if value is None else str(value)


def _candidate(draw: st.DrawFn, assets: list[Asset], on: date, held: Ledger) -> Transaction:
    asset = draw(st.sampled_from(assets))
    kind = draw(st.sampled_from(KINDS))
    at = draw(TIMES)
    cash = draw(st.booleans())
    if kind == "deposit":
        return deposit(on, str(draw(POSITIVE)), at=at)
    if kind == "withdrawal":
        return withdrawal(on, str(draw(POSITIVE)), at=at)
    if kind == "income":
        make = coupon if asset.is_bond else dividend
        return make(asset, on, str(draw(POSITIVE)), _maybe(draw, SMALL), affects_cash=cash, at=at)
    if kind == "split" and not asset.is_bond:
        return split(asset, on, str(draw(st.integers(1, 10))), str(draw(st.integers(1, 10))))
    position = held.positions.get(asset.id)
    if kind == "sell" and position is not None and position.quantity > 0:
        quantity = draw(st.decimals(min_value="0.00000001", max_value=position.quantity, places=8))
        return sell(
            asset,
            on,
            str(quantity),
            str(draw(AMOUNTS)),
            _maybe(draw, SMALL),
            _maybe(draw, SMALL) if asset.is_bond else None,
            _maybe(draw, SIGNED),
            affects_cash=cash,
            at=at,
        )
    return buy(
        asset,
        on,
        str(draw(QUANTITIES)),
        str(draw(AMOUNTS)),
        _maybe(draw, SMALL),
        _maybe(draw, SMALL) if asset.is_bond else None,
        _maybe(draw, SIGNED) if asset.is_bond else None,
        affects_cash=cash,
        at=at,
    )


@st.composite
def ledgers(draw: st.DrawFn) -> Generated:
    """A valid ledger: candidates that would break the replay (§4, D-69) are dropped."""
    assets = [*(etf() for _ in range(draw(st.integers(1, 2)))), bond()]
    by_id = assets_by_id(*assets)
    txs: list[Transaction] = []
    held = replay([])
    for day in range(draw(st.integers(1, 12))):
        on = START + timedelta(days=day)
        for _ in range(draw(st.integers(1, 3))):
            tx = _candidate(draw, assets, on, held)
            asset = by_id.get(tx.asset_id) if tx.asset_id else None
            assert validate_transaction(tx, asset) == [], tx
            try:
                held = replay([*txs, tx])
            except QuantityConflictError:
                continue
            txs.append(tx)
    if not txs:
        txs.append(deposit(START, "1"))
    return assets, txs


def _same_ledger(left: Ledger, right: Ledger) -> bool:
    return (left.positions, left.cash, left.warnings) == (
        right.positions,
        right.cash,
        right.warnings,
    )


def _of(asset: Asset, txs: list[Transaction], *types: TransactionType) -> list[Transaction]:
    return [tx for tx in txs if tx.asset_id == asset.id and tx.type in types]


def _total(values: object) -> Decimal:
    return sum(values, Decimal(0))  # type: ignore[call-overload,no-any-return]


# Ordering ------------------------------------------------------------------------


@SLOW
@given(ledgers(), st.randoms(use_true_random=False))
def test_replay_does_not_depend_on_input_order(generated: Generated, rnd: random.Random) -> None:
    _, txs = generated
    shuffled = txs[:]
    rnd.shuffle(shuffled)
    assert _same_ledger(replay(shuffled), replay(txs))


def _result(txs: list[Transaction]) -> object:
    try:
        ledger = replay(txs)
    except QuantityConflictError as exc:
        return (exc.code, exc.transaction_id)
    return (ledger.positions, ledger.cash, ledger.warnings)


@SLOW
@given(ledgers(), st.randoms(use_true_random=False))
def test_order_is_unique_even_with_equal_sequences(
    generated: Generated, rnd: random.Random
) -> None:
    _, txs = generated
    same = [dataclasses.replace(tx, sequence=1) for tx in txs]
    shuffled = same[:]
    rnd.shuffle(shuffled)
    assert _result(shuffled) == _result(same)  # equal outcome, even if it is a conflict


# Conservation rules ------------------------------------------------------------------


@SLOW
@given(ledgers())
def test_cost_is_conserved_and_never_negative(generated: Generated) -> None:
    assets, txs = generated
    ledger = replay(txs)
    for asset in assets:
        pos = ledger.positions.get(asset.id)
        if pos is None:
            continue
        added = _total(tx.gross_amount + or_zero(tx.fees) for tx in _of(asset, txs, T.BUY))
        proceeds = _total(tx.gross_amount - or_zero(tx.fees) for tx in _of(asset, txs, T.SELL))
        assert pos.cost_basis + pos.cost_removed == added
        assert pos.realized_gross == proceeds - pos.cost_removed
        assert pos.quantity >= 0 and pos.cost_basis >= 0
        if pos.quantity == 0:
            assert pos.cost_basis == 0


@SLOW
@given(ledgers())
def test_cash_is_the_sum_of_signed_totals_that_affect_cash(generated: Generated) -> None:
    _, txs = generated
    signs = {T.BUY: -1, T.WITHDRAWAL: -1, T.STOCK_SPLIT: 0}
    expected = _total(
        signs.get(tx.type, 1) * or_zero(tx.total_amount) for tx in txs if tx.affects_cash
    )
    assert replay(txs).cash == expected


@SLOW
@given(ledgers())
def test_income_and_taxes_follow_d42_and_d43(generated: Generated) -> None:
    assets, txs = generated
    ledger = replay(txs)
    for asset in assets:
        pos = ledger.positions.get(asset.id)
        if pos is None:
            continue
        income = _of(asset, txs, T.DIVIDEND, T.COUPON)
        buys, sells = _of(asset, txs, T.BUY), _of(asset, txs, T.SELL)
        assert pos.income_gross == (
            _total(tx.gross_amount for tx in income)
            + _total(or_zero(tx.accrued_interest_received) for tx in sells)
            - _total(or_zero(tx.accrued_interest_paid) for tx in buys)
        )
        assert pos.income_taxes == _total(or_zero(tx.taxes) for tx in [*income, *buys])
        assert pos.realized_taxes == _total(or_zero(tx.taxes) for tx in sells)
        assert pos.taxes == pos.income_taxes + pos.realized_taxes
        assert pos.realized_net == pos.realized_gross - pos.realized_taxes


@SLOW
@given(ledgers(), st.data())
def test_replay_as_of_equals_the_replay_of_the_prefix(
    generated: Generated, data: st.DataObject
) -> None:
    _, txs = generated
    last = max(tx.trade_date for tx in txs)
    day = START + timedelta(days=data.draw(st.integers(0, (last - START).days)))
    prefix = [tx for tx in txs if tx.trade_date <= day]
    assert _same_ledger(replay(txs, as_of=day), replay(prefix))


@SLOW
@given(ledgers(), st.integers(1, 10), st.integers(1, 10))
def test_split_keeps_cost_basis_and_realized(generated: Generated, new: int, old: int) -> None:
    assets, txs = generated
    before = replay(txs)
    last_day = max(tx.trade_date for tx in txs) + timedelta(days=1)
    splits = [split(a, last_day, str(new), str(old)) for a in assets if not a.is_bond]
    try:
        after = replay([*txs, *splits])
    except QuantityConflictError:
        reject()  # a split to zero is rejected (D-69); covered by the ledger tests
    for asset_id, pos in before.positions.items():
        assert after.positions[asset_id].cost_basis == pos.cost_basis
        assert after.positions[asset_id].realized_gross == pos.realized_gross
    assert after.cash == before.cash


# Edits ---------------------------------------------------------------------------


@SLOW
@given(ledgers(), st.data())
def test_edit_and_its_inverse_restore_the_ledger(generated: Generated, data: st.DataObject) -> None:
    assets, txs = generated
    assets_map = assets_by_id(*assets)
    target = data.draw(st.sampled_from(txs))
    changed = dataclasses.replace(target, trade_date=target.trade_date - timedelta(days=1000))
    outcome = edit(txs, assets_map, changed)
    if not outcome.accepted:  # moving it earlier may still be valid or not; rejection keeps all
        assert outcome.transactions == tuple(txs)
        return
    restored = edit(outcome.transactions, assets_map, target)
    assert restored.accepted
    assert restored.transactions == tuple(txs)
    assert restored.ledger is not None and _same_ledger(restored.ledger, replay(txs))


@SLOW
@given(ledgers(), st.data())
def test_rejected_edits_change_nothing(generated: Generated, data: st.DataObject) -> None:
    assets, txs = generated
    buys = [tx for tx in txs if tx.type is T.BUY]
    if not buys:
        reject()
    target = data.draw(st.sampled_from(buys))
    smaller = dataclasses.replace(target, quantity=Decimal("0.00000001"))
    outcome = edit(txs, assets_by_id(*assets), smaller)
    if outcome.accepted:
        assert outcome.ledger is not None
        assert _same_ledger(outcome.ledger, replay(list(outcome.transactions)))
    else:
        assert outcome.transactions == tuple(txs)
        assert outcome.ledger is None and outcome.conflict is not None


@SLOW
@given(ledgers(), st.data())
def test_delete_all_for_asset_is_always_accepted(generated: Generated, data: st.DataObject) -> None:
    assets, txs = generated
    asset = data.draw(st.sampled_from(assets))
    outcome = delete_all_for_asset(txs, assets_by_id(*assets), asset.id)
    assert outcome.accepted
    assert outcome.ledger is not None
    assert asset.id not in outcome.ledger.positions
    before = replay(txs).positions
    for asset_id, pos in outcome.ledger.positions.items():
        assert pos == before[asset_id]


@SLOW
@given(ledgers())
def test_overselling_is_rejected_and_leaves_the_ledger_unchanged(generated: Generated) -> None:
    assets, txs = generated
    asset = assets[0]
    held = replay(txs).positions.get(asset.id)
    quantity = (held.quantity if held else Decimal(0)) + Decimal("0.00000001")
    last_day = max(tx.trade_date for tx in txs) + timedelta(days=1)
    outcome = create(txs, assets_by_id(*assets), sell(asset, last_day, str(quantity), "1"))
    assert not outcome.accepted
    assert outcome.transactions == tuple(txs)


@SLOW
@given(ledgers())
def test_bulk_edit_of_unchanged_transactions_is_a_no_op(generated: Generated) -> None:
    assets, txs = generated
    outcome = bulk_edit(txs, assets_by_id(*assets), txs)
    assert outcome.accepted
    assert outcome.transactions == tuple(txs)
