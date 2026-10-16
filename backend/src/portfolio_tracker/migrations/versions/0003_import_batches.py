"""Import batches (M3): one row per confirmed import, linked from its transactions.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("move_cash", sa.Boolean(), nullable=False),
        sa.Column("imported", sa.Integer(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("not_recognized", sa.Integer(), nullable=False),
        sa.Column("to_review_skipped", sa.Integer(), nullable=False),
        sa.Column("ambiguous", sa.Integer(), nullable=False),
        sa.Column("invalid", sa.Integer(), nullable=False),
        sa.Column("skipped_by_choice", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "source IN ('directa-xlsx', 'app-csv')", name=op.f("ck_import_batches_source")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_import_batches_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_import_batches_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_batches")),
    )
    with op.batch_alter_table("import_batches", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_import_batches_account_id"), ["account_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_import_batches_owner_id"), ["owner_id"], unique=False)

    # SQLite cannot add a foreign key to an existing table: batch mode rebuilds it, keeping
    # every row (the new column is null for transactions entered before M3).
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("import_batch_id", sa.String(length=36), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_transactions_import_batch_id"), ["import_batch_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_transactions_import_batch_id_import_batches"),
            "import_batches",
            ["import_batch_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_transactions_import_batch_id_import_batches"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_transactions_import_batch_id"))
        batch_op.drop_column("import_batch_id")

    with op.batch_alter_table("import_batches", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_import_batches_owner_id"))
        batch_op.drop_index(batch_op.f("ix_import_batches_account_id"))

    op.drop_table("import_batches")
