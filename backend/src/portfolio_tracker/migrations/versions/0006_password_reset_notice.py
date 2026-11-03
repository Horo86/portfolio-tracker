"""The notice after an administrator reset (D-165): when the Administrator (or the container
command) last reset the user's password, until the user replaces the temporary one.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_reset_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    # SQLite's own DROP COLUMN (3.35+): no table rebuild, as in 0005.
    op.execute("ALTER TABLE users DROP COLUMN password_reset_at")
