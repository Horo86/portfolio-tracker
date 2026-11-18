"""Security settings (1.1.0, D-180): the session durations and the lockout thresholds an
Administrator saves, and whether every user must use two-factor authentication.

One new, empty table: no row is written (the environment's values apply until the first save),
no stored row is transformed and nothing needs the network (D-168 (5)).

Never edit this revision after the v1.1.0 tag: every later change is a new revision
(D-168); `tests/fixtures/schema/1002.json` freezes what it creates.

Revision ID: 1002
Revises: 1001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1002"
down_revision: str | None = "1001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Written out, never imported: a revision must not change when the code does.
_BOUNDS = (
    ("session_idle_days", 1, 30),
    ("session_absolute_days", 1, 90),
    ("login_account_failures", 3, 20),
    ("login_address_failures", 10, 200),
    ("block_first_minutes", 1, 15),
    ("block_max_minutes", 15, 120),
)


def upgrade() -> None:
    op.create_table(
        "security_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        *(sa.Column(name, sa.Integer(), nullable=False) for name, _, _ in _BOUNDS),
        sa.Column(
            "require_2fa_all_users", sa.Boolean(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_security_settings_single_row")),
        *(
            sa.CheckConstraint(
                f"{name} BETWEEN {low} AND {high}", name=op.f(f"ck_security_settings_{name}_range")
            )
            for name, low, high in _BOUNDS
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security_settings")),
    )


def downgrade() -> None:
    """For development only (D-168: no downgrade is offered to users)."""
    op.drop_table("security_settings")
