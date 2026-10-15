import sqlite3
import uuid
from contextlib import closing
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select, text
from sqlalchemy.exc import OperationalError, StatementError

from portfolio_tracker.db import BEGIN_OPTION, create_db_engine
from portfolio_tracker.db.types import DecimalText, UuidText, canonical_decimal

metadata = MetaData()
sample = Table(
    "sample",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("amount", DecimalText),
    Column("ref", UuidText),
)


@pytest.fixture
def db(tmp_path: Path):
    path = tmp_path / "db" / "app.sqlite"
    engine = create_db_engine(path)
    metadata.create_all(engine)
    yield engine, path
    engine.dispose()


@pytest.mark.parametrize(
    "value",
    [
        "0.1",
        "33.33333333",
        "-2.44",
        "12345678901234567890",
        "12345678901234567890.12345678",
        "2.50",
        "0.00000001",
        "0",
    ],
)
def test_decimal_round_trip_is_exact(db, value: str) -> None:
    engine, path = db
    with engine.begin() as conn:
        conn.execute(insert(sample).values(amount=Decimal(value)))
        stored = conn.execute(select(sample.c.amount)).scalar_one()

    assert stored == Decimal(value)
    assert canonical_decimal(stored) == value  # scale kept: the broker's precision is preserved
    with closing(sqlite3.connect(path)) as raw:
        assert raw.execute("SELECT amount, typeof(amount) FROM sample").fetchone() == (
            value,
            "text",
        )


def test_decimal_is_stored_without_exponent() -> None:
    assert canonical_decimal(Decimal("1E+3")) == "1000"
    assert canonical_decimal(Decimal("1E-9")) == "0.000000001"
    assert canonical_decimal(Decimal("-0.00")) == "0.00"


@pytest.mark.parametrize("bad", [0.1, 1, "1.5", Decimal("NaN"), Decimal("Infinity")])
def test_decimal_column_rejects_non_decimals(db, bad) -> None:
    engine, _ = db
    with pytest.raises(StatementError), engine.begin() as conn:
        conn.execute(insert(sample).values(amount=bad))


def test_uuid_round_trip_and_rejection(db) -> None:
    engine, _ = db
    ref = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(insert(sample).values(ref=ref))
        assert conn.execute(select(sample.c.ref)).scalar_one() == ref
    with pytest.raises(StatementError), engine.begin() as conn:
        conn.execute(insert(sample).values(ref=str(ref)))


def test_immediate_transaction_takes_the_write_lock_at_begin(db) -> None:
    engine, _ = db
    writer = engine.execution_options(**{BEGIN_OPTION: "IMMEDIATE"})
    with writer.connect() as first:
        first.begin()
        first.execute(text("SELECT 1"))  # BEGIN IMMEDIATE already holds the lock
        with writer.connect() as second:
            second.connection.driver_connection.execute("PRAGMA busy_timeout=0")
            with pytest.raises(OperationalError, match="locked"):
                second.begin()
                second.execute(text("SELECT 1"))
        first.rollback()


def test_deferred_readers_do_not_block(db) -> None:
    engine, _ = db
    with engine.connect() as first, engine.connect() as second:
        first.begin()
        second.begin()
        assert first.execute(text("SELECT 1")).scalar() == 1
        assert second.execute(text("SELECT 1")).scalar() == 1
