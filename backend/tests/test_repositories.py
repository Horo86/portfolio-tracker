"""Repository layer: owner isolation, asset visibility, price merge, ORM <-> engine mapping.

Invented users, accounts, assets and amounts only.
"""

from datetime import date, time
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from portfolio_tracker.db import models
from portfolio_tracker.engine import model as engine
from portfolio_tracker.repositories import accounts, assets, audit, mapping, prices, users
from portfolio_tracker.repositories import transactions as txs
from portfolio_tracker.repositories.transactions import TransactionFilter


def user(session: Session, username: str, role: str = "INVESTOR") -> models.User:
    return users.add_user(session, username=username, role=role, password_hash="x", language="en")


def private_asset(session: Session, owner: models.User, name: str = "Example ETF"):
    return assets.add_private_asset(
        session,
        owner.id,
        name=name,
        asset_type="ETF",
        quote_currency="EUR",
        quote_unit="CURRENCY",
    )


def shared_asset(session: Session, name: str = "Shared Example Stock") -> models.Asset:
    asset = models.Asset(name=name, asset_type="STOCK", quote_currency="EUR", quote_unit="CURRENCY")
    session.add(asset)
    session.flush()
    return asset


def add_tx(session, account, *, type="DEPOSIT", day=date(2024, 1, 2), asset=None, **amounts):
    row = models.Transaction(
        account_id=account.id,
        asset_id=asset.id if asset else None,
        type=type,
        trade_date=day,
        sequence=txs.next_sequence(session, account.id),
        total_amount=amounts.pop("total_amount", Decimal("100.00")),
        **amounts,
    )
    session.add(row)
    session.flush()
    return row


@pytest.fixture
def world(db_session: Session):
    """Alice and Bob, one account each, one transaction each."""
    alice, bob = user(db_session, "alice"), user(db_session, "bob")
    a_acc = accounts.add_account(db_session, alice.id, name="Alice main", currency="EUR")
    b_acc = accounts.add_account(db_session, bob.id, name="Bob main", currency="USD")
    a_tx, b_tx = add_tx(db_session, a_acc), add_tx(db_session, b_acc)
    return alice, bob, a_acc, b_acc, a_tx, b_tx


def test_engine_and_schema_enums_agree() -> None:
    assert tuple(t.value for t in engine.TransactionType) == models.TRANSACTION_TYPES
    assert tuple(t.value for t in engine.AssetType) == models.ASSET_TYPES
    assert tuple(t.value for t in engine.QuoteUnit) == models.QUOTE_UNITS


# --- accounts ---------------------------------------------------------------------------


def test_accounts_are_listed_and_found_only_for_their_owner(db_session, world) -> None:
    alice, bob, a_acc, b_acc, *_ = world

    assert [a.id for a in accounts.list_accounts(db_session, alice.id)] == [a_acc.id]
    assert accounts.get_account(db_session, alice.id, a_acc.id) is a_acc
    assert accounts.get_account(db_session, alice.id, b_acc.id) is None
    assert accounts.get_account(db_session, bob.id, a_acc.id) is None


def test_transaction_count_drives_the_currency_lock(db_session, world) -> None:
    alice, _, a_acc, *_ = world
    empty = accounts.add_account(db_session, alice.id, name="Empty", currency="EUR")

    assert accounts.has_transactions(db_session, a_acc)
    assert accounts.count_transactions(db_session, a_acc) == 1
    assert not accounts.has_transactions(db_session, empty)


# --- transactions -----------------------------------------------------------------------


def test_transactions_of_another_owner_are_invisible(db_session, world) -> None:
    alice, _, _, b_acc, a_tx, b_tx = world

    assert txs.get_transaction(db_session, alice.id, a_tx.id) is a_tx
    assert txs.get_transaction(db_session, alice.id, b_tx.id) is None
    assert txs.get_transactions(db_session, alice.id, {a_tx.id, b_tx.id}) == [a_tx]
    assert txs.account_ledger(db_session, alice.id, b_acc.id) == []
    rows, total = txs.list_transactions(db_session, alice.id)
    assert rows == [a_tx] and total == 1
    rows, total = txs.list_transactions(
        db_session, alice.id, TransactionFilter(account_id=b_acc.id)
    )
    assert rows == [] and total == 0


def test_list_filters_order_and_pagination(db_session, world) -> None:
    alice, _, a_acc, *_ = world
    asset = private_asset(db_session, alice)
    buy = add_tx(db_session, a_acc, type="BUY", day=date(2024, 3, 1), asset=asset)
    later = add_tx(db_session, a_acc, type="WITHDRAWAL", day=date(2024, 5, 1))

    rows, total = txs.list_transactions(db_session, alice.id, limit=2)
    assert total == 3 and [r.id for r in rows] == [later.id, buy.id]  # newest first
    rows, _ = txs.list_transactions(db_session, alice.id, limit=2, offset=2)
    assert len(rows) == 1
    flt = TransactionFilter(asset_id=asset.id)
    assert txs.list_transactions(db_session, alice.id, flt)[0] == [buy]
    flt = TransactionFilter(types=("WITHDRAWAL", "DEPOSIT"))
    assert txs.list_transactions(db_session, alice.id, flt)[1] == 2
    flt = TransactionFilter(date_from=date(2024, 2, 1), date_to=date(2024, 4, 30))
    assert txs.list_transactions(db_session, alice.id, flt)[0] == [buy]


def test_next_sequence_is_per_account(db_session, world) -> None:
    alice, _, a_acc, b_acc, *_ = world
    other = accounts.add_account(db_session, alice.id, name="Second", currency="EUR")

    assert txs.next_sequence(db_session, a_acc.id) == 2
    assert txs.next_sequence(db_session, b_acc.id) == 2
    assert txs.next_sequence(db_session, other.id) == 1


def test_delete_transactions(db_session, world) -> None:
    alice, _, a_acc, _, a_tx, b_tx = world

    txs.delete_transactions(db_session, [a_tx])

    assert txs.account_ledger(db_session, alice.id, a_acc.id) == []
    assert db_session.get(models.Transaction, b_tx.id) is not None


# --- assets -----------------------------------------------------------------------------


def test_private_assets_are_visible_only_to_their_owner(db_session, world) -> None:
    alice, bob, *_ = world
    mine = private_asset(db_session, alice)

    assert assets.list_assets(db_session, alice.id) == [mine]
    assert assets.list_assets(db_session, bob.id) == []
    assert assets.get_asset(db_session, bob.id, mine.id) is None
    assert assets.get_usable_asset(db_session, bob.id, mine.id) is None
    assert assets.get_private_asset(db_session, bob.id, mine.id) is None
    assert assets.get_private_asset(db_session, alice.id, mine.id) is mine


def test_shared_asset_is_visible_only_to_users_who_hold_it(db_session, world) -> None:
    alice, bob, a_acc, *_ = world
    shared = shared_asset(db_session)

    # Nobody uses it yet: listing it would reveal nothing, but D-35 keeps lists minimal.
    assert assets.list_assets(db_session, alice.id) == []
    assert assets.get_usable_asset(db_session, alice.id, shared.id) is shared

    add_tx(db_session, a_acc, type="BUY", asset=shared)

    assert assets.list_assets(db_session, alice.id) == [shared]
    assert assets.get_asset(db_session, alice.id, shared.id) is shared
    # Bob must not learn that someone holds it (D-35).
    assert assets.list_assets(db_session, bob.id) == []
    assert assets.get_asset(db_session, bob.id, shared.id) is None
    # Shared assets are never editable by users.
    assert assets.get_private_asset(db_session, alice.id, shared.id) is None


def test_asset_reference_check(db_session, world) -> None:
    alice, _, a_acc, *_ = world
    used, unused = private_asset(db_session, alice, "Used"), private_asset(db_session, alice)
    add_tx(db_session, a_acc, type="BUY", asset=used)

    assert assets.is_referenced(db_session, used)
    assert not assets.is_referenced(db_session, unused)
    assert assets.assets_by_id(db_session, {used.id}) == {used.id: used}
    assert assets.assets_by_id(db_session, set()) == {}


# --- prices -----------------------------------------------------------------------------


def provider_price(session, asset, day, close):
    session.add(models.Price(asset_id=asset.id, day=day, close=Decimal(close), source="PROVIDER"))
    session.flush()


def test_manual_price_overrides_provider_only_for_its_owner(db_session, world) -> None:
    alice, bob, *_ = world
    shared = shared_asset(db_session)
    d1, d2 = date(2024, 1, 2), date(2024, 1, 3)
    provider_price(db_session, shared, d1, "10.00")
    provider_price(db_session, shared, d2, "11.00")
    prices.upsert_manual_price(db_session, alice.id, shared.id, d2, Decimal("12.50"))

    assert prices.closes_for(db_session, alice.id, {shared.id}) == {
        shared.id: {d1: Decimal("10.00"), d2: Decimal("12.50")}
    }
    assert prices.closes_for(db_session, bob.id, {shared.id}) == {
        shared.id: {d1: Decimal("10.00"), d2: Decimal("11.00")}
    }
    assert prices.closes_for(db_session, bob.id, set()) == {}


def test_manual_prices_are_isolated_and_upserted(db_session, world) -> None:
    alice, bob, *_ = world
    asset = shared_asset(db_session)
    day = date(2024, 2, 1)
    first = prices.upsert_manual_price(db_session, alice.id, asset.id, day, Decimal("5"))
    again = prices.upsert_manual_price(db_session, alice.id, asset.id, day, Decimal("6"))

    assert again.id == first.id and again.close == Decimal("6")
    assert prices.list_manual_prices(db_session, alice.id, asset.id) == [first]
    assert prices.list_manual_prices(db_session, bob.id, asset.id) == []
    assert prices.get_manual_price(db_session, bob.id, first.id) is None
    prices.delete_price(db_session, first)
    assert prices.list_manual_prices(db_session, alice.id, asset.id) == []


# --- users and audit --------------------------------------------------------------------


def test_users_lookup_is_case_insensitive_and_admins_are_counted(db_session) -> None:
    assert not users.any_user_exists(db_session)
    admin = user(db_session, "Admin", role="ADMINISTRATOR")
    user(db_session, "carol")

    assert users.any_user_exists(db_session)
    assert users.get_by_username(db_session, "aDMIN") is admin
    assert users.get_by_username(db_session, "nobody") is None
    assert [u.username for u in users.list_users(db_session)] == ["Admin", "carol"]
    assert users.count_active_administrators(db_session) == 1


def test_audit_entry_stores_exact_decimals_as_strings(db_session, world) -> None:
    alice, *_ = world
    tx_id = uuid4()
    entry = audit.record(
        db_session,
        "transaction.update",
        actor_id=alice.id,
        entity_type="transaction",
        entity_id=tx_id,
        before={"quantity": Decimal("10"), "trade_date": date(2024, 1, 2)},
        after={"quantity": Decimal("0.00000001"), "types": (engine.TransactionType.BUY,)},
    )
    db_session.expire(entry)

    assert entry.entity_id == str(tx_id)
    assert entry.before == {"quantity": "10", "trade_date": "2024-01-02"}
    assert entry.after == {"quantity": "0.00000001", "types": ["BUY"]}


# --- mapping ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tx",
    [
        engine.Transaction(
            id=uuid4(),
            type=engine.TransactionType.BUY,
            trade_date=date(2024, 1, 2),
            trade_time=time(10, 30),
            value_date=date(2024, 1, 4),
            sequence=7,
            asset_id=None,  # replaced with a real asset in the test
            quantity=Decimal("5000"),
            unit_price=Decimal("97.50"),
            gross_amount=Decimal("4875.00"),
            fees=Decimal("5.00"),
            accrued_interest_paid=Decimal("12.34"),
            taxes=Decimal("-2.44"),
            total_amount=Decimal("4889.90"),
            affects_cash=False,
        ),
        engine.Transaction(
            id=uuid4(),
            type=engine.TransactionType.STOCK_SPLIT,
            trade_date=date(2024, 6, 3),
            sequence=8,
            asset_id=None,
            split_ratio=engine.SplitRatio(new=Decimal("1"), old=Decimal("10")),
        ),
    ],
)
def test_engine_transaction_round_trips_through_the_database(db_session, world, tx) -> None:
    alice, _, a_acc, *_ = world
    asset = private_asset(db_session, alice)
    tx = engine.Transaction(
        **{f: getattr(tx, f) for f in tx.__slots__ if f != "asset_id"}, asset_id=asset.id
    )
    row = mapping.copy_to_row(tx, models.Transaction(account_id=a_acc.id))
    db_session.add(row)
    db_session.flush()
    db_session.expire(row)

    assert mapping.to_engine_transaction(row) == tx
    assert mapping.to_engine_asset(asset) == engine.Asset(
        asset.id, engine.AssetType.ETF, "EUR", engine.QuoteUnit.CURRENCY
    )
