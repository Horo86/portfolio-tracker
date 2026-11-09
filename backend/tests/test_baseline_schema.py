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
