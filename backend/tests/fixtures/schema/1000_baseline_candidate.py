"""Baseline (1.0.0-rc.2, D-173): the whole schema in one revision.

It replaces the preview revisions 0001 to 0006 (0.9.0 and 1.0.0-rc.1), whose data is refused at
start, never upgraded (`migrations_runner.PREVIEW_REVISIONS`). It creates the same tables,
columns, constraints and indexes as that chain did (`tests/test_baseline_schema.py`). Types are
plain SQL types: UUIDs are TEXT(36), money and quantities TEXT decimals (ADR-0011), timestamps
UTC DATETIME. Nothing is seeded here: the market data configuration is written at start.

Never edit this revision after the 1.0.0-rc.2 tag: every later change is a new revision (D-168).

Revision ID: 1000
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1000"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = sa.String(length=36)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=32, collation="NOCASE"), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("theme", sa.String(length=16), nullable=False),
        sa.Column("reporting_currency", sa.String(length=3), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
        sa.Column("password_reset_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("language IN ('it', 'en')", name=op.f("ck_users_language")),
        sa.CheckConstraint("role IN ('INVESTOR', 'ADMINISTRATOR')", name=op.f("ck_users_role")),
        sa.CheckConstraint(
            "theme IN ('sobrio', 'moderno', 'ambra', 'grafite')", name=op.f("ck_users_theme")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("external_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_accounts_owner_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
    )
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_accounts_owner_id"), ["owner_id"], unique=False)

    op.create_table(
        "instruments",
        sa.Column("id", UUID, nullable=False),
        sa.Column("isin", sa.String(length=12), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("price_class", sa.String(length=16), nullable=True),
        sa.Column("class_source", sa.String(length=32), nullable=True),
        sa.Column("postponed_since", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("listing_recheck_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "price_class IS NULL OR price_class IN ('ETF_STOCK', 'BOND')",
            name=op.f("ck_instruments_price_class"),
        ),
        sa.CheckConstraint(
            "(price_class IS NULL) = (class_source IS NULL)",
            name=op.f("ck_instruments_class_has_source"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
        sa.UniqueConstraint(
            "isin", "quote_currency", name=op.f("uq_instruments_isin_quote_currency")
        ),
    )
    op.create_table(
        "instrument_engine_checks",
        sa.Column("instrument_id", UUID, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=True),
        sa.Column("mismatch", sa.String(length=16), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=32), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('OK', 'NOT_FOUND', 'MISMATCH', 'FAILING')",
            name=op.f("ck_instrument_engine_checks_status"),
        ),
        sa.CheckConstraint(
            "mismatch IS NULL OR mismatch IN ('CURRENCY', 'UNIT', 'LEVEL')",
            name=op.f("ck_instrument_engine_checks_mismatch"),
        ),
        sa.CheckConstraint(
            "(mismatch IS NOT NULL) = (status = 'MISMATCH')",
            name=op.f("ck_instrument_engine_checks_mismatch_kind"),
        ),
        sa.CheckConstraint(
            "status != 'OK' OR symbol IS NOT NULL",
            name=op.f("ck_instrument_engine_checks_ok_has_symbol"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_instrument_engine_checks_instrument_id_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instrument_id", "provider", name=op.f("pk_instrument_engine_checks")
        ),
    )
    op.create_table(
        "instrument_listings",
        sa.Column("id", UUID, nullable=False),
        sa.Column("instrument_id", UUID, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("exchange", sa.String(length=64), nullable=True),
        sa.Column("exchange_code", sa.String(length=16), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("unit", sa.String(length=24), nullable=False),
        sa.Column("matched_by", sa.String(length=8), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("mismatch", sa.String(length=16), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=32), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('OK', 'NOT_FOUND', 'MISMATCH', 'FAILING')",
            name=op.f("ck_instrument_listings_status"),
        ),
        sa.CheckConstraint(
            "unit IN ('CURRENCY', 'MINOR_CURRENCY_UNIT', 'PERCENT_OF_NOMINAL')",
            name=op.f("ck_instrument_listings_unit"),
        ),
        sa.CheckConstraint(
            "mismatch IS NULL OR mismatch IN ('CURRENCY', 'UNIT', 'LEVEL')",
            name=op.f("ck_instrument_listings_mismatch"),
        ),
        sa.CheckConstraint(
            "(mismatch IS NOT NULL) = (status = 'MISMATCH')",
            name=op.f("ck_instrument_listings_mismatch_kind"),
        ),
        sa.CheckConstraint(
            "matched_by IS NULL OR matched_by IN ('ISIN', 'HINT')",
            name=op.f("ck_instrument_listings_matched_by"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_instrument_listings_instrument_id_instruments"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instrument_listings")),
        sa.UniqueConstraint(
            "instrument_id",
            "provider",
            "symbol",
            name=op.f("uq_instrument_listings_instrument_id_provider_symbol"),
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
            "kind IN ('DAILY', 'CATCH_UP', 'BACKFILL', 'MANUAL', 'RESOLVE')",
            name=op.f("ck_job_runs_kind"),
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
    op.create_table(
        "provider_settings",
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_provider_settings")),
    )
    op.create_table(
        "provider_routes",
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "role IN ('ETF_STOCK', 'BOND', 'FX', 'CLASSIFY')", name=op.f("ck_provider_routes_role")
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_provider_routes_position")),
        sa.PrimaryKeyConstraint("role", "position", name=op.f("pk_provider_routes")),
        sa.UniqueConstraint("role", "provider", name=op.f("uq_provider_routes_role_provider")),
    )
    op.create_table(
        "market_data_config",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("exchanges", sa.JSON(), nullable=False),
        sa.Column("seeded_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("resolve_requested_at", sa.DateTime(), nullable=True),
        sa.Column("resolve_attempts", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_market_data_config_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_data_config")),
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=True),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column("ticker", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("asset_type", sa.String(length=8), nullable=False),
        sa.Column("quote_currency", sa.String(length=3), nullable=False),
        sa.Column("quote_unit", sa.String(length=24), nullable=False),
        sa.Column("provider_symbols", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("instrument_id", sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "asset_type IN ('ETF', 'STOCK', 'BOND')", name=op.f("ck_assets_asset_type")
        ),
        sa.CheckConstraint(
            "quote_unit IN ('CURRENCY', 'MINOR_CURRENCY_UNIT', 'PERCENT_OF_NOMINAL')",
            name=op.f("ck_assets_quote_unit"),
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_assets_owner_id_users")),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_assets_instrument_id_instruments"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
    )
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_assets_isin"), ["isin"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_assets_instrument_id"), ["instrument_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_assets_owner_id"), ["owner_id"], unique=False)
        batch_op.create_index(
            "uq_assets_private_isin",
            ["owner_id", "isin"],
            unique=True,
            sqlite_where=sa.text("owner_id IS NOT NULL AND isin IS NOT NULL"),
        )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.String(length=36), nullable=True),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_audit_log_actor_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_log_at"), ["at"], unique=False)

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
    )
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sessions_user_id"), ["user_id"], unique=False)

    op.create_table(
        "prices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("close", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(source = 'MANUAL') = (owner_id IS NOT NULL)", name=op.f("ck_prices_manual_has_owner")
        ),
        sa.CheckConstraint("source IN ('PROVIDER', 'MANUAL')", name=op.f("ck_prices_source")),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], name=op.f("fk_prices_asset_id_assets"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_prices_owner_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prices")),
    )
    with op.batch_alter_table("prices", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_prices_owner_id"), ["owner_id"], unique=False)
        batch_op.create_index(
            "uq_prices_manual_day",
            ["asset_id", "owner_id", "day"],
            unique=True,
            sqlite_where=sa.text("source = 'MANUAL'"),
        )
        batch_op.create_index(
            "uq_prices_provider_day",
            ["asset_id", "day"],
            unique=True,
            sqlite_where=sa.text("source = 'PROVIDER'"),
        )

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

    op.create_table(
        "transactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=True),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("trade_time", sa.Time(), nullable=True),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Text(), nullable=True),
        sa.Column("unit_price", sa.Text(), nullable=True),
        sa.Column("gross_amount", sa.Text(), nullable=True),
        sa.Column("fees", sa.Text(), nullable=True),
        sa.Column("accrued_interest_paid", sa.Text(), nullable=True),
        sa.Column("accrued_interest_received", sa.Text(), nullable=True),
        sa.Column("taxes", sa.Text(), nullable=True),
        sa.Column("total_amount", sa.Text(), nullable=True),
        sa.Column("split_new", sa.Text(), nullable=True),
        sa.Column("split_old", sa.Text(), nullable=True),
        sa.Column("affects_cash", sa.Boolean(), nullable=False),
        sa.Column("external_ref", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("import_batch_id", sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "type IN ('BUY', 'SELL', 'DIVIDEND', 'COUPON', 'DEPOSIT', 'WITHDRAWAL', 'STOCK_SPLIT')",
            name=op.f("ck_transactions_type"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_transactions_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], name=op.f("fk_transactions_asset_id_assets")
        ),
        sa.ForeignKeyConstraint(
            ["import_batch_id"],
            ["import_batches.id"],
            name=op.f("fk_transactions_import_batch_id_import_batches"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transactions")),
        sa.UniqueConstraint(
            "account_id", "sequence", name=op.f("uq_transactions_account_id_sequence")
        ),
    )
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_transactions_account_id_trade_date"),
            ["account_id", "trade_date"],
            unique=False,
        )
        batch_op.create_index(batch_op.f("ix_transactions_asset_id"), ["asset_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_transactions_import_batch_id"), ["import_batch_id"], unique=False
        )


def downgrade() -> None:
    """For development only (D-168: no downgrade is offered to users)."""
    for table in (
        "transactions",
        "import_batches",
        "prices",
        "sessions",
        "audit_log",
        "assets",
        "market_data_config",
        "provider_routes",
        "provider_settings",
        "provider_credentials",
        "job_runs",
        "fx_rates",
        "instrument_prices",
        "instrument_listings",
        "instrument_engine_checks",
        "instruments",
        "accounts",
        "users",
    ):
        op.drop_table(table)
