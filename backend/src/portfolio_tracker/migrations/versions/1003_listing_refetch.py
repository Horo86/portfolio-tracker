"""The refetch mark of a listing (1.2.0, D-201): download its closes again, whole.

One nullable column on `instrument_listings`, then every existing listing is marked with the
time of the migration, so the background loop downloads each series again once and stores it
on today's scale (a series stored before 1.2.0 may mix levels across a split). Both statements
run in the single migration transaction; nothing here needs the network: the migration only
marks the rows for the background loop (D-168 (5)).

Never edit this revision after the v1.2.0 tag: every later change is a new revision
(D-168); `tests/fixtures/schema/1003.json` freezes what it creates.

Revision ID: 1003
Revises: 1002
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "1003"
down_revision: str | None = "1002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "instrument_listings", sa.Column("refetch_requested_at", sa.DateTime(), nullable=True)
    )
    # Stored as the app stores every time: UTC, without an offset (`db.types.UtcDateTime`).
    now = datetime.now(UTC).replace(tzinfo=None)
    op.execute(
        sa.text("UPDATE instrument_listings SET refetch_requested_at = :now").bindparams(
            sa.bindparam("now", now, type_=sa.DateTime())
        )
    )


def downgrade() -> None:
    """For development only (D-168: no downgrade is offered to users)."""
    with op.batch_alter_table("instrument_listings") as batch:
        batch.drop_column("refetch_requested_at")
