"""Migration 0002 matches the ORM models and enforces the key constraints."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portfolio_tracker.db import create_db_engine
from portfolio_tracker.db.models import Account, Asset, Base, Price, Transaction, User
from portfolio_tracker.migrations_runner import alembic_config, run_migrations

TABLES = {"users", "sessions", "accounts", "assets", "transactions", "prices", "audit_log"}


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db_path = tmp_path / "db" / "app.sqlite"
    engine = create_db_engine(db_path)
    run_migrations(engine, db_path, tmp_path / "backups")
    yield engine
    engine.dispose()


def make_user(session: Session, username: str = "alice") -> User:
    user = User(username=username, role="INVESTOR", password_hash="x", language="en")
    session.add(user)
    session.flush()
    return user


def make_asset(session: Session, owner: User | None) -> Asset:
    asset = Asset(
        owner_id=owner.id if owner else None,
        name="Example World ETF",
        asset_type="ETF",
        quote_currency="EUR",
        quote_unit="CURRENCY",
    )
    session.add(asset)
    session.flush()
    return asset


def test_migrations_create_every_table(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) >= TABLES


def test_models_and_migrations_do_not_diverge(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(context, Base.metadata)

    assert diff == []


def test_upgrade_from_0001_and_downgrade_back(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "app.sqlite"
    engine = create_db_engine(db_path)
    config = alembic_config(db_path)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
    assert not TABLES & set(inspect(engine).get_table_names())

    copy = run_migrations(engine, db_path, tmp_path / "backups")

    assert copy is not None  # existing database with a pending revision: copied first
    assert set(inspect(engine).get_table_names()) >= TABLES
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0001")
    assert not TABLES & set(inspect(engine).get_table_names())
    engine.dispose()


def test_username_is_unique_ignoring_case(engine: Engine) -> None:
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        make_user(session, "Alice")
        make_user(session, "aLICE")


def test_insertion_sequence_is_unique_per_account(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        user = make_user(session)
        first, second = (Account(owner_id=user.id, name=n, currency="EUR") for n in "AB")
        session.add_all([first, second])
        session.flush()
        for account in (first, second):  # the same sequence in two accounts is fine
            session.add(
                Transaction(
                    account_id=account.id,
                    type="DEPOSIT",
                    trade_date=date(2024, 1, 2),
                    sequence=1,
                    total_amount=Decimal("100.00"),
                )
            )
        session.flush()
        account_id = first.id

    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.add(
            Transaction(
                account_id=account_id,
                type="DEPOSIT",
                trade_date=date(2024, 1, 3),
                sequence=1,
                total_amount=Decimal("5.00"),
            )
        )


def test_deleting_an_account_deletes_its_transactions(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        user = make_user(session)
        account = Account(owner_id=user.id, name="A", currency="EUR")
        session.add(account)
        session.flush()
        account_id = account.id
        session.add(
            Transaction(
                account_id=account_id,
                type="DEPOSIT",
                trade_date=date(2024, 1, 2),
                sequence=1,
                total_amount=Decimal("1.00"),
            )
        )
    with Session(engine) as session, session.begin():
        session.delete(session.get(Account, account_id))
    with Session(engine) as session:
        assert session.query(Transaction).count() == 0


def test_manual_price_needs_an_owner_and_provider_price_none(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        asset_id = make_asset(session, None).id
        owner_id = make_user(session).id
    bad = [
        Price(asset_id=asset_id, day=date(2024, 1, 2), close=Decimal(1), source="MANUAL"),
        Price(
            asset_id=asset_id,
            day=date(2024, 1, 2),
            close=Decimal(1),
            source="PROVIDER",
            owner_id=owner_id,
        ),
    ]
    for price in bad:
        with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
            session.add(price)


def test_one_price_per_day_per_source_and_owner(engine: Engine) -> None:
    day = date(2024, 1, 2)
    with Session(engine) as session, session.begin():
        asset_id = make_asset(session, None).id
        alice, bob = make_user(session, "alice").id, make_user(session, "bob").id
        session.add_all(
            [
                Price(asset_id=asset_id, day=day, close=Decimal("10"), source="PROVIDER"),
                Price(asset_id=asset_id, day=day, close=Decimal("11"), source="MANUAL",
                      owner_id=alice),
                Price(asset_id=asset_id, day=day, close=Decimal("12"), source="MANUAL",
                      owner_id=bob),
            ]
        )  # fmt: skip
    duplicates = [
        Price(asset_id=asset_id, day=day, close=Decimal("9"), source="PROVIDER"),
        Price(asset_id=asset_id, day=day, close=Decimal("9"), source="MANUAL", owner_id=alice),
    ]
    for price in duplicates:
        with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
            session.add(price)


def test_timestamps_round_trip_in_utc(engine: Engine) -> None:
    rome = datetime(2024, 6, 1, 12, 0, tzinfo=UTC).astimezone(ZoneInfo("Europe/Rome"))
    with Session(engine) as session, session.begin():
        user = make_user(session)
        user.last_login_at = rome
        user_id = user.id
    with Session(engine) as session:
        stored = session.get(User, user_id).last_login_at

    assert stored == rome
    assert stored.utcoffset() == timedelta(0)
    assert isinstance(user_id, uuid.UUID)
