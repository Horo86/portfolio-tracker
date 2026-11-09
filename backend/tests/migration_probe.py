"""A test-only revision above the shipped head (D-168, D-173): what the first real migration
after the baseline will do, tested before one exists. Installed in a copy of the migrations
folder by the `probe` fixture (`conftest.py`); nothing of it ships in the package."""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from portfolio_tracker import migrations_runner

PROBE = '''"""Test-only revision: a table, then {what}."""
import sqlalchemy as sa
from alembic import op

revision = "{revision}"
down_revision = "{down}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("upgrade_probe", sa.Column("id", sa.Integer, primary_key=True))
    {body}


def downgrade() -> None:
    op.drop_table("upgrade_probe")
'''

# The shipped head (the baseline `1000` until a later revision exists) and the test revision.
HEAD = migrations_runner.head_revision()
PROBE_REVISION = "probe"
Probe = Callable[[bool], None]


def make_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Probe:
    """Install revision `probe` (failing or not) above the head, in a copy of the migrations
    folder."""
    folder = tmp_path / "migrations"

    def install(fails: bool) -> None:
        if not folder.exists():
            shutil.copytree(
                migrations_runner.MIGRATIONS_DIR,
                folder,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        body = 'raise RuntimeError("the probe failed on purpose")' if fails else "pass"
        what = "a failure" if fails else "nothing"
        (folder / "versions" / "probe.py").write_text(
            PROBE.format(body=body, what=what, revision=PROBE_REVISION, down=HEAD)
        )
        shutil.rmtree(folder / "versions" / "__pycache__", ignore_errors=True)
        monkeypatch.setattr(migrations_runner, "MIGRATIONS_DIR", folder)

    return install
