"""ORM models (docs/domain-model.md). Sources of truth only: no derived data is stored.

Money and quantities are `DecimalText`, ids are `UuidText`, timestamps are UTC. Enum-like
columns are plain strings with CHECK constraints, so a new value is a small migration.
"""

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from portfolio_tracker.db.types import DecimalText, UtcDateTime, UuidText

# Deterministic constraint names, so Alembic batch migrations can find them on SQLite.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ROLES = ("INVESTOR", "ADMINISTRATOR")
LANGUAGES = ("it", "en")
THEMES = ("sobrio", "moderno", "ambra", "grafite")  # D-76; default moderno
TRANSACTION_TYPES = ("BUY", "SELL", "DIVIDEND", "COUPON", "DEPOSIT", "WITHDRAWAL", "STOCK_SPLIT")
ASSET_TYPES = ("ETF", "STOCK", "BOND")
QUOTE_UNITS = ("CURRENCY", "MINOR_CURRENCY_UNIT", "PERCENT_OF_NOMINAL")
PRICE_SOURCES = ("PROVIDER", "MANUAL")
IMPORT_SOURCES = ("directa-xlsx", "app-csv")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def new_id() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in("role", ROLES), name="role"),
        CheckConstraint(_in("language", LANGUAGES), name="language"),
        CheckConstraint(_in("theme", THEMES), name="theme"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UuidText, primary_key=True, default=new_id)
    # ASCII only (validated by the API), so NOCASE makes uniqueness case-insensitive.
    username: Mapped[str] = mapped_column(String(32, collation="NOCASE"), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(default=False)
    language: Mapped[str] = mapped_column(String(2))
    theme: Mapped[str] = mapped_column(String(16), default="moderno")
    reporting_currency: Mapped[str] = mapped_column(String(3), default="EUR")
    password_changed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    disabled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class UserSession(Base):
    """Opaque session: only the SHA-256 of the token is stored (ADR-0005)."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UuidText, primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UuidText, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)  # absolute expiry
    user_agent: Mapped[str | None] = mapped_column(String(300))


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UuidText, primary_key=True, default=new_id)
    owner_id: Mapped[uuid.UUID] = mapped_column(UuidText, ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3))
    external_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class Asset(Base):
    """Shared reference data when `owner_id` is NULL (provider-resolved, M4), else private."""

    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint(_in("asset_type", ASSET_TYPES), name="asset_type"),
        CheckConstraint(_in("quote_unit", QUOTE_UNITS), name="quote_unit"),
        # One private asset per ISIN and user (M2 decision); shared assets and assets
        # without ISIN are not constrained here.
        Index(
            "uq_assets_private_isin",
            "owner_id",
            "isin",
            unique=True,
            sqlite_where=text("owner_id IS NOT NULL AND isin IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UuidText, primary_key=True, default=new_id)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UuidText, ForeignKey("users.id"), index=True)
    isin: Mapped[str | None] = mapped_column(String(12), index=True)
    ticker: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    asset_type: Mapped[str] = mapped_column(String(8))
    quote_currency: Mapped[str] = mapped_column(String(3))
    quote_unit: Mapped[str] = mapped_column(String(24))
    provider_symbols: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class Transaction(Base):
    """Primary source of truth (domain-model.md, domain-rules.md §2)."""

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "sequence"),  # D-70
        Index(None, "account_id", "trade_date"),
        CheckConstraint(_in("type", TRANSACTION_TYPES), name="type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UuidText, primary_key=True, default=new_id)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UuidText, ForeignKey("accounts.id", ondelete="CASCADE")
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidText, ForeignKey("assets.id"), index=True
    )
    type: Mapped[str] = mapped_column(String(16))
    trade_date: Mapped[date]
    trade_time: Mapped[time | None]
    value_date: Mapped[date | None]
    sequence: Mapped[int]
    quantity: Mapped[Decimal | None] = mapped_column(DecimalText)
    unit_price: Mapped[Decimal | None] = mapped_column(DecimalText)
    gross_amount: Mapped[Decimal | None] = mapped_column(DecimalText)
    fees: Mapped[Decimal | None] = mapped_column(DecimalText)
    accrued_interest_paid: Mapped[Decimal | None] = mapped_column(DecimalText)
    accrued_interest_received: Mapped[Decimal | None] = mapped_column(DecimalText)
    taxes: Mapped[Decimal | None] = mapped_column(DecimalText)
    total_amount: Mapped[Decimal | None] = mapped_column(DecimalText)
    split_new: Mapped[Decimal | None] = mapped_column(DecimalText)
    split_old: Mapped[Decimal | None] = mapped_column(DecimalText)
    affects_cash: Mapped[bool] = mapped_column(default=True)
    external_ref: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    # The import that created it (M3); kept, as null, if the batch row is ever removed.
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UuidText, ForeignKey("import_batches.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class ImportBatch(Base):
    """One confirmed import into one account (domain-model.md, D-38, D-109). Created only at
    confirmation: previews store nothing (D-100). The file itself is never stored."""

    __tablename__ = "import_batches"
    __table_args__ = (CheckConstraint(_in("source", IMPORT_SOURCES), name="source"),)

    id: Mapped[uuid.UUID] = mapped_column(UuidText, primary_key=True, default=new_id)
    owner_id: Mapped[uuid.UUID] = mapped_column(UuidText, ForeignKey("users.id"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UuidText, ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(16))
    file_name: Mapped[str | None] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64))
    move_cash: Mapped[bool]  # the "do not move cash" option was off (D-49, D-112)
    imported: Mapped[int]
    duplicates: Mapped[int]  # already present, skipped
    not_recognized: Mapped[int]
    to_review_skipped: Mapped[int]  # "to review" rows the user did not select (D-99)
    ambiguous: Mapped[int]
    invalid: Mapped[int]
    skipped_by_choice: Mapped[int]  # selectable rows left out by the user
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class Price(Base):
    """Daily close in the asset quote unit. Provider prices are shared; a manual price
    belongs to its owner and affects only that user's figures (D-35)."""

    __tablename__ = "prices"
    __table_args__ = (
        CheckConstraint(_in("source", PRICE_SOURCES), name="source"),
        CheckConstraint("(source = 'MANUAL') = (owner_id IS NOT NULL)", name="manual_has_owner"),
        # NULLs are distinct in SQLite unique constraints: one partial index per source.
        Index(
            "uq_prices_provider_day",
            "asset_id",
            "day",
            unique=True,
            sqlite_where=text("source = 'PROVIDER'"),
        ),
        Index(
            "uq_prices_manual_day",
            "asset_id",
            "owner_id",
            "day",
            unique=True,
            sqlite_where=text("source = 'MANUAL'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UuidText, primary_key=True, default=new_id)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UuidText, ForeignKey("assets.id", ondelete="CASCADE")
    )
    day: Mapped[date]
    close: Mapped[Decimal] = mapped_column(DecimalText)
    source: Mapped[str] = mapped_column(String(8))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UuidText, ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class AuditEntry(Base):
    """Append-only internal log (domain-model.md); browsing UI is V2."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UuidText, primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UuidText, ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(36))
    before: Mapped[Any | None] = mapped_column(JSON)
    after: Mapped[Any | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(64))
