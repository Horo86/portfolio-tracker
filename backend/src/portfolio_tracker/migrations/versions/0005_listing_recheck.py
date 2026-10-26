"""Listing re-check marks (D-152): the listing rank now puts the main exchanges trading in
the instrument's currency first, so every instrument existing at the upgrade is marked to be
searched again; the market data runs take up a few marked instruments each.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-26
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("instruments", sa.Column("listing_recheck_at", sa.DateTime(), nullable=True))
    marked = datetime.now(UTC).replace(tzinfo=None)  # stored as naive UTC, as UtcDateTime does
    op.execute(sa.text("UPDATE instruments SET listing_recheck_at = :at").bindparams(at=marked))


def downgrade() -> None:
    # SQLite's own DROP COLUMN (3.35+): no table rebuild, so the rows that reference
    # instruments are never touched.
    op.execute("ALTER TABLE instruments DROP COLUMN listing_recheck_at")
