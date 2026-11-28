"""The baseline revision `1000` creates the schema of the preview chain (D-173).

The preview revisions `0001` to `0006` were deleted once this test had proven, against the chain
itself, that the baseline creates the same schema (the commit before their deletion). Their
schema stays frozen in `tests/fixtures/schema/0006.json` (`schema_description.py`), so the
baseline is still compared with it: any difference must be declared here, with its decision.
The only one is the users table of D-173 (1): username and its key, names and e-mail.
"""

from pathlib import Path

import pytest
import schema_description
from alembic import command
from alembic.script import ScriptDirectory

from portfolio_tracker.db import create_db_engine
from portfolio_tracker.migrations_runner import (
    PREVIEW_REVISIONS,
    alembic_config,
    run_migrations,
)

SCHEMA = Path(__file__).parent / "fixtures" / "schema"


@pytest.fixture
def baseline(tmp_path: Path) -> dict:
    db_path = tmp_path / "db" / "app.sqlite"
    engine = create_db_engine(db_path)
    try:
        config = alembic_config(db_path)
        with engine.begin() as connection:  # the baseline alone, not the revisions above it
            config.attributes["connection"] = connection
            command.upgrade(config, "1000")
    finally:
        engine.dispose()
    return schema_description.describe(db_path)


@pytest.fixture
def revision_1001(tmp_path: Path) -> dict:
    db_path = tmp_path / "r1001" / "app.sqlite"
    engine = create_db_engine(db_path)
    try:
        config = alembic_config(db_path)
        with engine.begin() as connection:  # up to 1001, not to whatever the head is later
            config.attributes["connection"] = connection
            command.upgrade(config, "1001")
    finally:
        engine.dispose()
    return schema_description.describe(db_path)


@pytest.fixture
def revision_1002(tmp_path: Path) -> dict:
    db_path = tmp_path / "r1002" / "app.sqlite"
    engine = create_db_engine(db_path)
    try:
        config = alembic_config(db_path)
        with engine.begin() as connection:  # up to 1002, not to whatever the head is later
            config.attributes["connection"] = connection
            command.upgrade(config, "1002")
    finally:
        engine.dispose()
    return schema_description.describe(db_path)


@pytest.fixture
def revision_1003(tmp_path: Path) -> dict:
    db_path = tmp_path / "r1003" / "app.sqlite"
    engine = create_db_engine(db_path)
    try:
        config = alembic_config(db_path)
        with engine.begin() as connection:  # up to 1003, not to whatever the head is later
            config.attributes["connection"] = connection
            command.upgrade(config, "1003")
    finally:
        engine.dispose()
    return schema_description.describe(db_path)


@pytest.fixture
def head(tmp_path: Path) -> dict:
    db_path = tmp_path / "head" / "app.sqlite"
    engine = create_db_engine(db_path)
    try:
        run_migrations(engine, db_path, tmp_path / "backups")
    finally:
        engine.dispose()
    return schema_description.describe(db_path)


def test_the_baseline_is_the_first_revision_and_no_preview_revision_ships() -> None:
    script = ScriptDirectory.from_config(alembic_config(Path("unused.sqlite")))
    revisions = {revision.revision: revision for revision in script.walk_revisions()}
    assert script.get_bases() == ["1000"] and revisions["1000"].down_revision is None
    assert not set(revisions) & PREVIEW_REVISIONS


def test_the_baseline_creates_the_schema_of_the_preview_chain(baseline: dict) -> None:
    """Every table as the chain made it, except the users table of D-173 (1) below."""
    chain = schema_description.read(SCHEMA / "0006.json")
    assert sorted(baseline) == sorted(chain)
    for table in chain:
        if table != "users":
            assert baseline[table] == chain[table], table


def test_the_users_table_differs_only_by_the_user_details_of_d_173(baseline: dict) -> None:
    chain, users = schema_description.read(SCHEMA / "0006.json")["users"], baseline["users"]
    assert set(chain["columns"]) - set(users["columns"]) == {
        "username VARCHAR(32) COLLATE NOCASE NOT NULL",
        "display_name VARCHAR(100)",
    }
    assert set(users["columns"]) - set(chain["columns"]) == {
        "username VARCHAR(64) NOT NULL",
        "username_key VARCHAR(192) NOT NULL",
        "first_name VARCHAR(100) NOT NULL",
        "last_name VARCHAR(100) NOT NULL",
        "email VARCHAR(254) NOT NULL",
    }
    assert set(chain["constraints"]) - set(users["constraints"]) == {
        "CONSTRAINT uq_users_username UNIQUE (username)"
    }
    assert set(users["constraints"]) - set(chain["constraints"]) == {
        "CONSTRAINT ck_users_email_lowercase CHECK (email = lower(email))",
        "CONSTRAINT uq_users_email UNIQUE (email)",
        "CONSTRAINT uq_users_username_key UNIQUE (username_key)",
    }
    assert users["foreign_keys"] == chain["foreign_keys"] == []
    assert users["without_rowid"] is chain["without_rowid"] is False
    unique = sorted(i["columns"][0][0] for i in users["indexes"].values() if i["unique"])
    assert unique == ["email", "id", "username_key"]


def test_the_baseline_is_frozen_since_its_release(baseline: dict) -> None:
    """1.0.0-rc.2 shipped the baseline `1000` (D-173): installations carry it, so it is never
    edited again. A schema change is a new revision above it (D-168), and this test fails on
    any change to what `1000` creates (`tests/fixtures/schema/1000.json`)."""
    frozen = schema_description.read(SCHEMA / "1000.json")
    assert sorted(baseline) == sorted(frozen)
    for table in frozen:
        assert baseline[table] == frozen[table], table


def test_revision_1001_is_frozen_since_its_release(revision_1001: dict) -> None:
    """1.0.0-rc.3 shipped `1001` (D-174): installations carry it, so it is never edited again.
    A schema change is a new revision above it (D-168), and this test fails on any change to
    what the chain creates up to `1001` (`tests/fixtures/schema/1001.json`)."""
    frozen = schema_description.read(SCHEMA / "1001.json")
    assert sorted(revision_1001) == sorted(frozen)
    for table in frozen:
        assert revision_1001[table] == frozen[table], table


def test_revision_1001_only_adds_two_factor_authentication(
    baseline: dict, revision_1001: dict
) -> None:
    """D-174: two new tables and one nullable column on users; every other table is as the
    baseline made it, so no stored row is touched by the upgrade from 1.0.0-rc.2. Compared
    with `1001` itself, not the head, which gains tables in later revisions."""
    head = revision_1001
    assert sorted(set(head) - set(baseline)) == ["recovery_codes", "two_factor"]
    for table in baseline:
        if table != "users":
            assert head[table] == baseline[table], table
    for part in ("constraints", "foreign_keys", "indexes", "without_rowid"):
        assert head["users"][part] == baseline["users"][part], part
    assert head["users"]["column_info"][:-1] == baseline["users"]["column_info"]
    added = set(head["users"]["columns"]) - set(baseline["users"]["columns"])
    assert added == {"two_factor_disabled_at DATETIME"}
    assert head["two_factor"]["foreign_keys"] and head["recovery_codes"]["foreign_keys"]


def test_revision_1002_is_frozen_since_its_release(revision_1002: dict) -> None:
    """1.1.0 ships `1002` (D-180): once tagged it is never edited again. This test fails on any
    change to what the chain creates up to `1002` (`tests/fixtures/schema/1002.json`)."""
    frozen = schema_description.read(SCHEMA / "1002.json")
    assert sorted(revision_1002) == sorted(frozen)
    for table in frozen:
        assert revision_1002[table] == frozen[table], table


def test_revision_1002_only_adds_security_settings(
    revision_1001: dict, revision_1002: dict
) -> None:
    """D-180: one new table; every other table is as `1001` made it, so the upgrade from 1.0.2
    touches no stored row."""
    assert sorted(set(revision_1002) - set(revision_1001)) == ["security_settings"]
    for table in revision_1001:
        assert revision_1002[table] == revision_1001[table], table
    assert revision_1002["security_settings"]["foreign_keys"] == []


def test_revision_1003_is_frozen_since_its_release(revision_1003: dict) -> None:
    """1.2.0 ships `1003` (D-201): once tagged it is never edited again. This test fails on any
    change to what the chain creates up to `1003` (`tests/fixtures/schema/1003.json`)."""
    frozen = schema_description.read(SCHEMA / "1003.json")
    assert sorted(revision_1003) == sorted(frozen)
    for table in frozen:
        assert revision_1003[table] == frozen[table], table


def test_revision_1003_only_adds_the_refetch_mark(revision_1002: dict, revision_1003: dict) -> None:
    """D-201: one nullable column on the listings; every other table is as `1002` made it."""
    assert sorted(revision_1003) == sorted(revision_1002)
    for table in revision_1002:
        if table != "instrument_listings":
            assert revision_1003[table] == revision_1002[table], table
    before, after = revision_1002["instrument_listings"], revision_1003["instrument_listings"]
    assert after["columns"] == [*before["columns"], "refetch_requested_at DATETIME"]
    [added] = after["column_info"][len(before["column_info"]) :]
    assert after["column_info"][: len(before["column_info"])] == before["column_info"]
    assert added[1:4] == ["refetch_requested_at", "DATETIME", 0]  # nullable, no default
    for key in before:
        if key not in ("columns", "column_info"):
            assert after[key] == before[key], key
