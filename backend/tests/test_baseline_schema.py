"""The baseline revision `1000` creates the schema of the preview chain (D-173).

Before the preview revisions `0001` to `0006` are deleted, the baseline (still a candidate in
`tests/fixtures/schema/`) and that chain migrate two empty databases, and their descriptions
(`schema_description.py`) must be equal. The description of the chain is also frozen in
`tests/fixtures/schema/0006.json`, so the check keeps working once the chain is gone.
"""

import shutil
from pathlib import Path

import pytest
import schema_description

from portfolio_tracker import migrations_runner
from portfolio_tracker.db import create_db_engine
from portfolio_tracker.migrations_runner import run_migrations

SCHEMA = Path(__file__).parent / "fixtures" / "schema"
CANDIDATE = SCHEMA / "1000_baseline_candidate.py"


def migrated(tmp_path: Path, name: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / name / "app.sqlite"
    engine = create_db_engine(db_path)
    try:
        run_migrations(engine, db_path, tmp_path / name / "backups")
    finally:
        engine.dispose()
    return db_path


def only_the_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "migrations"
    shutil.copytree(
        migrations_runner.MIGRATIONS_DIR,
        folder,
        ignore=shutil.ignore_patterns("__pycache__", "0*.py"),
    )
    shutil.copy(CANDIDATE, folder / "versions" / "1000_baseline.py")
    monkeypatch.setattr(migrations_runner, "MIGRATIONS_DIR", folder)


def test_the_baseline_creates_the_schema_of_the_preview_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chain = schema_description.describe(migrated(tmp_path, "chain", monkeypatch))
    assert chain == schema_description.read(SCHEMA / "0006.json")

    only_the_baseline(tmp_path, monkeypatch)
    baseline = schema_description.describe(migrated(tmp_path, "baseline", monkeypatch))
    assert sorted(baseline) == sorted(chain)
    for table in chain:
        assert baseline[table] == chain[table], table
