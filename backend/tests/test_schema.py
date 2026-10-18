"""Migrations 0002-0004 match the ORM models and enforce the key constraints."""

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portfolio_tracker.db import create_db_engine, models
from portfolio_tracker.db.models import (
    Account,
    Asset,
    Base,
    ImportBatch,
    Price,
    Transaction,
    User,
)
from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.base import ErrorCode, MatchSource, PriceClass, Role
from portfolio_tracker.marketdata.units import MismatchKind
from portfolio_tracker.migrations_runner import (
    MigrationIntegrityError,
    alembic_config,
    run_migrations,
)

TABLES = {"users", "sessions", "accounts", "assets", "transactions", "prices", "audit_log",
          "import_batches", "instruments", "instrument_engine_checks", "instrument_listings",
          "instrument_prices",
          "fx_rates", "job_runs", "provider_credentials", "provider_settings",
          "provider_routes", "market_data_config"}  # fmt: skip


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


# --- Import batches (migration 0003) --------------------------------------------------------


def make_batch(session: Session, user: User, account: Account, **fields) -> ImportBatch:
    counts = dict.fromkeys(("imported", "duplicates", "not_recognized", "to_review_skipped",
                            "ambiguous", "invalid", "skipped_by_choice"), 0)  # fmt: skip
    defaults = counts | {"source": "directa-xlsx", "file_sha256": "0" * 64, "move_cash": True}
    batch = ImportBatch(owner_id=user.id, account_id=account.id, **(defaults | fields))
    session.add(batch)
    session.flush()
    return batch


def deposit(account: Account, sequence: int, batch: ImportBatch | None = None) -> Transaction:
    return Transaction(account_id=account.id, type="DEPOSIT", trade_date=date(2024, 1, 2),
                       sequence=sequence, total_amount=Decimal("1.00"),
                       import_batch_id=batch.id if batch else None)  # fmt: skip


def test_upgrade_0002_to_0003_keeps_existing_transactions(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "app.sqlite"
    engine = create_db_engine(db_path)
    config = alembic_config(db_path)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0002")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users (id, username, role, password_hash, must_change_password, "
            "language, theme, reporting_currency, password_changed_at, created_at) VALUES "
            "('u1', 'alice', 'INVESTOR', 'x', 0, 'en', 'moderno', 'EUR', "
            "'2024-01-01 00:00:00', '2024-01-01 00:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO accounts (id, owner_id, name, currency, created_at) VALUES "
            "('a1', 'u1', 'Main', 'EUR', '2024-01-01 00:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO transactions (id, account_id, type, trade_date, sequence, total_amount, "
            "affects_cash, created_at, updated_at) VALUES ('t1', 'a1', 'DEPOSIT', '2024-01-02', "
            "1, '100.00', 1, '2024-01-02 00:00:00', '2024-01-02 00:00:00')"
        )

    assert run_migrations(engine, db_path, tmp_path / "backups") is not None  # copied first
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT id, total_amount, import_batch_id FROM transactions"
        ).fetchall()
    assert rows == [("t1", "100.00", None)]

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0002")
    assert "import_batches" not in inspect(engine).get_table_names()
    columns = {c["name"] for c in inspect(engine).get_columns("transactions")}
    assert "import_batch_id" not in columns
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM transactions").scalar() == 1
    engine.dispose()


def test_the_alembic_command_line_keeps_manual_prices_across_0004(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review M-3: `alembic upgrade head` and `alembic downgrade 0003` from the command line
    (env.py without the app's connection) rebuild `assets` with foreign keys off, so the
    manual prices that reference it are neither deleted nor blocking."""
    data_dir = tmp_path / "data"
    db_path = data_dir / "db" / "app.sqlite"
    engine = create_db_engine(db_path)
    config = alembic_config(db_path)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0003")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users (id, username, role, password_hash, must_change_password, "
            "language, theme, reporting_currency, password_changed_at, created_at) VALUES "
            "('u1', 'alice', 'INVESTOR', 'x', 0, 'en', 'moderno', 'EUR', "
            "'2024-01-01 00:00:00', '2024-01-01 00:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO assets (id, owner_id, isin, name, asset_type, quote_currency, "
            "quote_unit, created_at) VALUES ('s1', 'u1', 'ZZ0000000018', 'Example ETF', 'ETF', "
            "'EUR', 'CURRENCY', '2024-01-01 00:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO prices (id, asset_id, day, close, source, owner_id, created_at) VALUES "
            "('p1', 's1', '2024-01-05', '10.50', 'MANUAL', 'u1', '2024-01-05 00:00:00')"
        )
    engine.dispose()
    monkeypatch.setenv("PT_DATA_DIR", str(data_dir))
    command_line = Config(str(Path(__file__).parents[1] / "alembic.ini"))  # as `alembic` does

    def state() -> tuple[list, set[str]]:
        reader = create_db_engine(db_path)
        try:
            with reader.connect() as connection:
                rows = connection.exec_driver_sql("SELECT id, close FROM prices").fetchall()
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            return rows, {c["name"] for c in inspect(reader).get_columns("assets")}
        finally:
            reader.dispose()

    command.upgrade(command_line, "head")
    rows, columns = state()
    assert rows == [("p1", "10.50")] and "instrument_id" in columns

    command.downgrade(command_line, "0003")
    rows, columns = state()
    assert rows == [("p1", "10.50")] and "instrument_id" not in columns


def test_deleting_an_account_deletes_its_import_batches(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        user = make_user(session)
        account = Account(owner_id=user.id, name="A", currency="EUR")
        session.add(account)
        session.flush()
        batch = make_batch(session, user, account, imported=1)
        session.add(deposit(account, 1, batch))
        account_id = account.id
    with Session(engine) as session, session.begin():
        session.delete(session.get(Account, account_id))
    with Session(engine) as session:
        assert session.query(ImportBatch).count() == 0
        assert session.query(Transaction).count() == 0


def test_removing_a_batch_row_keeps_its_transactions(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        user = make_user(session)
        account = Account(owner_id=user.id, name="A", currency="EUR")
        session.add(account)
        session.flush()
        batch = make_batch(session, user, account, imported=1)
        session.add(deposit(account, 1, batch))
        batch_id = batch.id
    with engine.begin() as connection:  # bypass the ORM: the database rule itself
        connection.exec_driver_sql("DELETE FROM import_batches WHERE id = ?", (str(batch_id),))
    with Session(engine) as session:
        assert session.query(Transaction).one().import_batch_id is None


def test_import_batch_source_is_checked(engine: Engine) -> None:
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        user = make_user(session)
        account = Account(owner_id=user.id, name="A", currency="EUR")
        session.add(account)
        session.flush()
        make_batch(session, user, account, source="fineco-xlsx")


def test_upgrade_to_0004_keeps_assets_and_their_isin_index(tmp_path: Path) -> None:
    """0004 rebuilds `assets` (batch mode) to add `instrument_id`: rows, prices and the
    partial unique index of private ISINs must survive."""
    db_path = tmp_path / "db" / "app.sqlite"
    engine = create_db_engine(db_path)
    config = alembic_config(db_path)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0003")
        connection.exec_driver_sql(
            "INSERT INTO users (id, username, role, password_hash, must_change_password, "
            "language, theme, reporting_currency, password_changed_at, created_at) VALUES "
            "('00000000-0000-4000-8000-000000000001', 'alice', 'INVESTOR', 'x', 0, 'en', "
            "'moderno', 'EUR', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO assets (id, owner_id, isin, name, asset_type, quote_currency, "
            "quote_unit, created_at) VALUES ('00000000-0000-4000-8000-000000000002', "
            "'00000000-0000-4000-8000-000000000001', 'ZZ0000000418', 'Example', 'ETF', 'EUR', "
            "'CURRENCY', '2026-01-01 00:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO prices (id, asset_id, day, close, source, owner_id, created_at) VALUES "
            "('00000000-0000-4000-8000-000000000003', '00000000-0000-4000-8000-000000000002', "
            "'2026-01-02', '10.50', 'MANUAL', '00000000-0000-4000-8000-000000000001', "
            "'2026-01-02 00:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO accounts (id, owner_id, name, currency, created_at) VALUES "
            "('00000000-0000-4000-8000-000000000004', '00000000-0000-4000-8000-000000000001', "
            "'Example', 'EUR', '2026-01-01 00:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO transactions (id, account_id, asset_id, type, trade_date, sequence, "
            "quantity, total_amount, affects_cash, created_at, updated_at) VALUES "
            "('00000000-0000-4000-8000-000000000005', '00000000-0000-4000-8000-000000000004', "
            "'00000000-0000-4000-8000-000000000002', 'BUY', '2026-01-02', 1, '1', '-10.50', 1, "
            "'2026-01-02 00:00:00', '2026-01-02 00:00:00')"
        )

    run_migrations(engine, db_path, tmp_path / "backups")

    with Session(engine) as session:
        asset = session.get(Asset, uuid.UUID("00000000-0000-4000-8000-000000000002"))
        assert asset is not None and asset.instrument_id is None
        assert session.query(Price).one().close == Decimal("10.50")
        assert session.query(Transaction).one().asset_id == asset.id
    with engine.connect() as connection:  # restored for the app's connections
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.add(
            Asset(
                owner_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
                isin="ZZ0000000418",
                name="Duplicate",
                asset_type="ETF",
                quote_currency="EUR",
                quote_unit="CURRENCY",
            )
        )
    engine.dispose()


def test_an_upgrade_leaving_broken_references_is_rolled_back(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "app.sqlite"
    engine = create_db_engine(db_path)
    config = alembic_config(db_path)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0003")
    with engine.connect() as connection:  # a dangling reference, written without the checks
        driver = connection.connection.driver_connection
        driver.execute("PRAGMA foreign_keys=OFF")
        driver.execute(
            "INSERT INTO prices (id, asset_id, day, close, source, created_at) VALUES "
            "('00000000-0000-4000-8000-000000000009', '00000000-0000-4000-8000-00000000dead', "
            "'2026-01-02', '1', 'PROVIDER', '2026-01-02 00:00:00')"
        )
        driver.execute("PRAGMA foreign_keys=ON")

    with pytest.raises(MigrationIntegrityError, match="1 broken"):
        run_migrations(engine, db_path, tmp_path / "backups")

    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_heads() == ("0003",)
        assert "instruments" not in inspect(engine).get_table_names()
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    engine.dispose()


# --- engine configuration and routing columns (D-132 to D-134) ------------------------------


def sql_refused(engine: Engine, statement: str) -> None:
    """The database itself refuses `statement` (written without the ORM)."""
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.exec_driver_sql(statement)


INSTRUMENT = (
    "INSERT INTO instruments (id, isin, quote_currency, price_class, class_source, created_at) "
    "VALUES ('00000000-0000-4000-8000-000000000001', 'ZZ0000000418', 'EUR', {}, "
    "'2026-01-02 00:00:00')"
)
LISTING = (  # {}: status, mismatch, matched_by
    "INSERT INTO instrument_listings (id, instrument_id, provider, symbol, currency, unit, "
    "status, mismatch, matched_by, is_active, failure_count, created_at) VALUES "
    "('00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000001', "
    "'alpha', 'XMPW.MI', 'EUR', 'CURRENCY', {}, 0, 0, '2026-01-02 00:00:00')"
)
CHECK = (  # {}: status, mismatch
    "INSERT INTO instrument_engine_checks (instrument_id, provider, status, mismatch, "
    "failure_count) VALUES ('00000000-0000-4000-8000-000000000001', 'alpha', {}, 0)"
)


def test_an_instrument_class_is_known_and_has_a_source(engine: Engine) -> None:
    sql_refused(engine, INSTRUMENT.format("'FUND', 'alpha'"))
    sql_refused(engine, INSTRUMENT.format("'BOND', NULL"))
    sql_refused(engine, INSTRUMENT.format("NULL, 'alpha'"))
    with engine.begin() as connection:
        connection.exec_driver_sql(INSTRUMENT.format("'BOND', 'UNIT'"))


def test_level_mismatches_and_resolve_jobs_are_allowed(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(INSTRUMENT.format("NULL, NULL"))
        connection.exec_driver_sql(LISTING.format("'MISMATCH', 'LEVEL', NULL"))
        connection.exec_driver_sql(
            "UPDATE instrument_listings SET matched_by = 'HINT' WHERE provider = 'alpha'"
        )
        connection.exec_driver_sql(
            "INSERT INTO job_runs (id, kind, started_at, result) VALUES "
            "('00000000-0000-4000-8000-000000000003', 'RESOLVE', '2026-01-02 00:00:00', 'OK')"
        )


def test_provider_routes_are_checked(engine: Engine) -> None:
    insert = "INSERT INTO provider_routes (role, position, provider) VALUES "
    with engine.begin() as connection:
        connection.exec_driver_sql(insert + "('BOND', 0, 'alpha'), ('ETF_STOCK', 0, 'alpha')")
    sql_refused(engine, insert + "('PRICE', 0, 'beta')")  # unknown role
    sql_refused(engine, insert + "('FX', -1, 'beta')")
    sql_refused(engine, insert + "('BOND', 1, 'alpha')")  # twice in one order
    sql_refused(engine, insert + "('BOND', 0, 'beta')")  # two engines in one place


def test_the_market_data_configuration_is_a_single_row(engine: Engine) -> None:
    insert = (
        "INSERT INTO market_data_config (id, exchanges, seeded_at, updated_at, "
        "resolve_attempts) VALUES ({}, '[]', '2026-01-02 00:00:00', '2026-01-02 00:00:00', 0)"
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(insert.format(1))
    sql_refused(engine, insert.format(2))


def test_a_listing_records_only_known_match_sources(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(INSTRUMENT.format("NULL, NULL"))
    sql_refused(engine, LISTING.format("'OK', NULL, 'NAME'"))


def test_an_engine_check_has_known_values(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(INSTRUMENT.format("NULL, NULL"))
    sql_refused(engine, CHECK.format("'GONE', NULL"))
    sql_refused(engine, CHECK.format("'OK', NULL"))  # an accepted listing names its symbol
    sql_refused(engine, CHECK.format("'MISMATCH', NULL"))
    sql_refused(engine, CHECK.format("'NOT_FOUND', 'UNIT'"))
    with engine.begin() as connection:
        connection.exec_driver_sql(CHECK.format("'MISMATCH', 'CURRENCY'"))
        connection.exec_driver_sql(
            CHECK.format("'MISMATCH', 'LEVEL'").replace("'alpha'", "'beta'")
        )  # a listing refused by the level check (D-134)


def test_a_listing_mismatch_has_a_kind(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(INSTRUMENT.format("NULL, NULL"))
    sql_refused(engine, LISTING.format("'MISMATCH', NULL, NULL"))
    sql_refused(engine, LISTING.format("'OK', 'UNIT', NULL"))
    sql_refused(engine, LISTING.format("'OK', NULL, NULL").replace("'CURRENCY'", "'PERCENT'"))


def test_check_values_follow_the_code_enums() -> None:
    """Review nit: `compare_metadata` does not compare CHECK constraints, so the value lists
    are pinned to the enums the code uses."""
    assert set(models.PRICE_CLASSES) == set(PriceClass)
    assert set(models.PROVIDER_ROLES) == set(Role)
    assert set(models.MATCH_SOURCES) == set(MatchSource)
    assert set(models.QUOTE_UNITS) == set(QuoteUnit)
    assert set(models.MISMATCH_KINDS) == set(MismatchKind)
    assert max(len(code) for code in ErrorCode) <= 32  # last_error_code columns


@pytest.mark.parametrize(
    ("table", "column", "values"),
    [
        ("instruments", "price_class", "PRICE_CLASSES"),
        ("instrument_engine_checks", "status", "LISTING_STATUSES"),
        ("instrument_engine_checks", "mismatch", "MISMATCH_KINDS"),
        ("instrument_listings", "status", "LISTING_STATUSES"),
        ("instrument_listings", "mismatch", "MISMATCH_KINDS"),
        ("instrument_listings", "matched_by", "MATCH_SOURCES"),
        ("instrument_listings", "unit", "QUOTE_UNITS"),
        ("job_runs", "kind", "JOB_KINDS"),
        ("job_runs", "result", "JOB_RESULTS"),
        ("provider_routes", "role", "PROVIDER_ROLES"),
    ],
)
def test_the_migration_checks_allow_exactly_the_values_of_the_model(
    engine: Engine, table: str, column: str, values: str
) -> None:
    """The column's own `IN (...)` list in the migrated table, not any quoted word in it."""
    with engine.connect() as connection:
        sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).scalar_one()
    (listed,) = re.findall(rf"\b{column} IN \(([^)]*)\)", sql)
    allowed = [value.strip().strip("'") for value in listed.split(",")]
    assert sorted(allowed) == sorted(getattr(models, values))
