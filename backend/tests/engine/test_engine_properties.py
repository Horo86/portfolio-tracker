"""Property tests for ordering, cost conservation and edits (ADR-0010, D-54). Invented data."""

import dataclasses
import random
from datetime import date, timedelta
from decimal import Decimal

from factories import assets_by_id, buy, deposit, dividend, etf, sell, split, withdrawal
from hypothesis import given, settings
from hypothesis import strategies as st

from portfolio_tracker.engine.edits import bulk_edit, create, delete_all_for_asset, edit
from portfolio_tracker.engine.ledger import Ledger, replay
from portfolio_tracker.engine.model import Asset, Transaction, TransactionType

Generated = tuple[list[Asset], list[Transaction]]

START = date(2020, 1, 1)
QUANTITIES = st.decimals(min_value="0.00000001", max_value="1000", places=8)
AMOUNTS = st.decimals(min_value="0", max_value="100000", places=2)
FEES = st.decimals(min_value="0", max_value="20", places=2)


@st.composite
def ledgers(draw: st.DrawFn) -> Generated:
    """A valid ledger: one transaction per day, sells never exceed the held quantity."""
    assets = [etf() for _ in range(draw(st.integers(1, 3)))]
    held = {asset.id: Decimal(0) for asset in assets}
    txs: list[Transaction] = []
    for day in range(draw(st.integers(1, 25))):
        on = START + timedelta(days=day)
        asset = draw(st.sampled_from(assets))
        kind = draw(
            st.sampled_from(["buy", "buy", "sell", "split", "dividend", "deposit", "withdrawal"])
        )
        if kind == "sell" and held[asset.id] > 0:
            quantity = draw(st.decimals(min_value="0.00000001", max_value=held[asset.id], places=8))
            txs.append(sell(asset, on, str(quantity), str(draw(AMOUNTS)), str(draw(FEES))))
            held[asset.id] -= quantity
        elif kind == "split":
            new, old = draw(st.integers(1, 10)), draw(st.integers(1, 10))
            txs.append(split(asset, on, str(new), str(old)))
            held[asset.id] = replay(txs).positions[asset.id].quantity
        elif kind == "dividend":
            txs.append(dividend(asset, on, str(draw(AMOUNTS) + Decimal("0.01"))))
        elif kind == "deposit":
            txs.append(deposit(on, str(draw(AMOUNTS) + Decimal("0.01"))))
        elif kind == "withdrawal":
            txs.append(withdrawal(on, str(draw(AMOUNTS) + Decimal("0.01"))))
        else:
            quantity = draw(QUANTITIES)
            txs.append(buy(asset, on, str(quantity), str(draw(AMOUNTS)), str(draw(FEES))))
            held[asset.id] += quantity
    return assets, txs


def _same_ledger(left: Ledger, right: Ledger) -> bool:
    return (left.positions, left.cash, left.warnings) == (
        right.positions,
        right.cash,
        right.warnings,
    )


@given(ledgers(), st.randoms(use_true_random=False))
def test_replay_does_not_depend_on_input_order(generated: Generated, rnd: random.Random) -> None:
    _, txs = generated
    shuffled = txs[:]
    rnd.shuffle(shuffled)
    assert _same_ledger(replay(shuffled), replay(txs))


@given(ledgers())
def test_cost_is_conserved_and_never_negative(generated: Generated) -> None:
    assets, txs = generated
    ledger = replay(txs)
    for asset in assets:
        added = sum(
            (
                tx.gross_amount + tx.fees
                for tx in txs
                if tx.asset_id == asset.id and tx.type is TransactionType.BUY
            ),
            Decimal(0),
        )
        proceeds = sum(
            (
                tx.gross_amount - tx.fees
                for tx in txs
                if tx.asset_id == asset.id and tx.type is TransactionType.SELL
            ),
            Decimal(0),
        )
        pos = ledger.positions.get(asset.id)
        if pos is None:
            continue
        assert pos.cost_basis + pos.cost_removed == added
        assert pos.realized_gross == proceeds - pos.cost_removed
        assert pos.quantity >= 0 and pos.cost_basis >= 0
        if pos.quantity == 0:
            assert pos.cost_basis == 0


@given(ledgers(), st.integers(1, 10), st.integers(1, 10))
def test_split_keeps_cost_basis_and_realized(generated: Generated, new: int, old: int) -> None:
    assets, txs = generated
    before = replay(txs)
    last_day = max(tx.trade_date for tx in txs) + timedelta(days=1)
    after = replay([*txs, *(split(a, last_day, str(new), str(old)) for a in assets)])
    for asset_id, pos in before.positions.items():
        assert after.positions[asset_id].cost_basis == pos.cost_basis
        assert after.positions[asset_id].realized_gross == pos.realized_gross
    assert after.cash == before.cash


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


@settings(max_examples=50)
@given(ledgers())
def test_bulk_edit_of_unchanged_transactions_is_a_no_op(generated: Generated) -> None:
    assets, txs = generated
    outcome = bulk_edit(txs, assets_by_id(*assets), txs)
    assert outcome.accepted
    assert outcome.transactions == tuple(txs)
