"""Domain schema (M2): users, sessions, accounts, assets, transactions, prices, audit log.

Types are frozen as plain SQL types: UUIDs are TEXT(36), money and quantities are TEXT
decimals (ADR-0011), timestamps are UTC DATETIME. Derived data is never stored.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
        sa.CheckConstraint(
            "asset_type IN ('ETF', 'STOCK', 'BOND')", name=op.f("ck_assets_asset_type")
        ),
        sa.CheckConstraint(
            "quote_unit IN ('CURRENCY', 'MINOR_CURRENCY_UNIT', 'PERCENT_OF_NOMINAL')",
            name=op.f("ck_assets_quote_unit"),
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_assets_owner_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
    )
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_assets_isin"), ["isin"], unique=False)
        batch_op.create_index(batch_op.f("ix_assets_owner_id"), ["owner_id"], unique=False)

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


def downgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_transactions_asset_id"))
        batch_op.drop_index(batch_op.f("ix_transactions_account_id_trade_date"))

    op.drop_table("transactions")
    with op.batch_alter_table("prices", schema=None) as batch_op:
        batch_op.drop_index("uq_prices_provider_day", sqlite_where=sa.text("source = 'PROVIDER'"))
        batch_op.drop_index("uq_prices_manual_day", sqlite_where=sa.text("source = 'MANUAL'"))
        batch_op.drop_index(batch_op.f("ix_prices_owner_id"))

    op.drop_table("prices")
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sessions_user_id"))

    op.drop_table("sessions")
    with op.batch_alter_table("audit_log", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_log_at"))

    op.drop_table("audit_log")
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_assets_owner_id"))
        batch_op.drop_index(batch_op.f("ix_assets_isin"))

    op.drop_table("assets")
    with op.batch_alter_table("accounts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_accounts_owner_id"))

    op.drop_table("accounts")
    op.drop_table("users")
