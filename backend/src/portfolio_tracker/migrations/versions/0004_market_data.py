"""Market data (M4): instruments, listings, provider closes, FX rates, job runs, provider
credentials, and the link from assets to instruments (D-116 to D-129).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = sa.String(length=36)


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", UUID, nullable=False),
        sa.Column("isin", sa.String(length=12), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
        sa.UniqueConstraint(
            "isin", "quote_currency", name=op.f("uq_instruments_isin_quote_currency")
        ),
    )
    op.create_table(
        "instrument_listings",
        sa.Column("id", UUID, nullable=False),
        sa.Column("instrument_id", UUID, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=True),
        sa.Column("exchange", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("unit", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("mismatch", sa.String(length=16), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=32), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('OK', 'NOT_FOUND', 'MISMATCH', 'FAILING')",
            name=op.f("ck_instrument_listings_status"),
        ),
        sa.CheckConstraint(
            "unit IS NULL OR unit IN ('CURRENCY', 'MINOR_CURRENCY_UNIT', 'PERCENT_OF_NOMINAL')",
            name=op.f("ck_instrument_listings_unit"),
        ),
        sa.CheckConstraint(
            "mismatch IS NULL OR mismatch IN ('CURRENCY', 'UNIT')",
            name=op.f("ck_instrument_listings_mismatch"),
        ),
        sa.CheckConstraint(
            "NOT is_active OR (symbol IS NOT NULL AND currency IS NOT NULL AND unit IS NOT NULL)",
            name=op.f("ck_instrument_listings_active_is_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_instrument_listings_instrument_id_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instrument_listings")),
        sa.UniqueConstraint(
            "instrument_id", "provider", name=op.f("uq_instrument_listings_instrument_id_provider")
        ),
    )
    op.create_index(
        "uq_instrument_listings_active",
        "instrument_listings",
        ["instrument_id"],
        unique=True,
        sqlite_where=sa.text("is_active"),
    )
    op.create_table(
        "instrument_prices",
        sa.Column("listing_id", UUID, nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("close", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["instrument_listings.id"],
            name=op.f("fk_instrument_prices_listing_id_instrument_listings"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("listing_id", "day", name=op.f("pk_instrument_prices")),
        sqlite_with_rowid=False,
    )
    op.create_table(
        "fx_rates",
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("rate", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("currency", "day", name=op.f("pk_fx_rates")),
        sqlite_with_rowid=False,
    )
    op.create_table(
        "job_runs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=True),
        sa.Column("errors", sa.JSON(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('DAILY', 'CATCH_UP', 'BACKFILL', 'MANUAL')", name=op.f("ck_job_runs_kind")
        ),
        sa.CheckConstraint(
            "result IN ('RUNNING', 'OK', 'PARTIAL', 'FAILED', 'INTERRUPTED')",
            name=op.f("ck_job_runs_result"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_runs")),
    )
    op.create_index(op.f("ix_job_runs_started_at"), "job_runs", ["started_at"], unique=False)
    op.create_table(
        "provider_credentials",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("provider", name=op.f("pk_provider_credentials")),
    )

    # SQLite cannot add a foreign key to an existing table: batch mode rebuilds it, keeping
    # every row (no asset is linked until the market data service resolves it).
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("instrument_id", UUID, nullable=True))
        batch_op.create_index(batch_op.f("ix_assets_instrument_id"), ["instrument_id"])
        batch_op.create_foreign_key(
            batch_op.f("fk_assets_instrument_id_instruments"),
            "instruments",
            ["instrument_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_assets_instrument_id_instruments"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_assets_instrument_id"))
        batch_op.drop_column("instrument_id")

    op.drop_table("provider_credentials")
    op.drop_index(op.f("ix_job_runs_started_at"), table_name="job_runs")
    op.drop_table("job_runs")
    op.drop_table("fx_rates")
    op.drop_table("instrument_prices")
    op.drop_index("uq_instrument_listings_active", table_name="instrument_listings")
    op.drop_table("instrument_listings")
    op.drop_table("instruments")
