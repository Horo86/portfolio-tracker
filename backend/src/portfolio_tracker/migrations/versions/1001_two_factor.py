"""Two-factor authentication (1.0.0-rc.3, D-174): the TOTP secrets, the recovery codes and
the date of an administrator's removal.

Only new, empty tables and one nullable column: no stored row is transformed and nothing needs
the network (D-168 (5)). After this revision nobody has two-factor authentication; with the
default `PT_REQUIRE_2FA=admins` administrators enrol at their next request.

Revision ID: 1001
Revises: 1000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1001"
down_revision: str | None = "1000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "two_factor",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("secret", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("last_step", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_two_factor_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_two_factor")),
    )
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_recovery_codes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recovery_codes")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_recovery_codes_code_hash")),
    )
    op.create_index(op.f("ix_recovery_codes_user_id"), "recovery_codes", ["user_id"], unique=False)
    op.add_column("users", sa.Column("two_factor_disabled_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """For development only (D-168: no downgrade is offered to users)."""
    op.drop_column("users", "two_factor_disabled_at")
    op.drop_index(op.f("ix_recovery_codes_user_id"), table_name="recovery_codes")
    op.drop_table("recovery_codes")
    op.drop_table("two_factor")
