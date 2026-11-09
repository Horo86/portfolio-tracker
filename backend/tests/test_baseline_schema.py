"""The baseline revision `1000` creates the schema of the preview chain (D-173).

The preview revisions `0001` to `0006` were deleted once this test had proven, against the chain
itself, that the baseline creates the same schema (the commit before their deletion). Their
schema stays frozen in `tests/fixtures/schema/0006.json` (`schema_description.py`), so the
baseline is still compared with it: any difference must be declared here, with its decision.
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
    chain = schema_description.read(SCHEMA / "0006.json")
    assert sorted(baseline) == sorted(chain)
    for table in chain:
        assert baseline[table] == chain[table], table
