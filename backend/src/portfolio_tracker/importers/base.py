"""Upload limits, file errors, exact spreadsheet numbers (D-101, D-105) and the shapes every
importer returns (the `Importer` protocol)."""

import base64
import binascii
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, time
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from portfolio_tracker.engine.decimals import div8
from portfolio_tracker.engine.model import AssetType, QuoteUnit, SplitRatio, TransactionType

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB (mockup 07)
MAX_BASE64_CHARS = 4 * math.ceil(MAX_FILE_BYTES / 3)


class ImportFileError(Exception):
    """The file cannot be read at all. `code` is stable (ADR-0004); `detail` is a developer
    hint that never contains file content (it may hold personal data)."""

    def __init__(self, code: str, detail: str, **params: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.params = params  # e.g. the CSV column or row concerned, for the UI


def too_large(detail: str) -> ImportFileError:
    return ImportFileError("IMPORT_TOO_LARGE", detail)


def invalid(detail: str) -> ImportFileError:
    return ImportFileError("IMPORT_FILE_INVALID", detail)


def decode_upload(content_base64: str) -> bytes:
    """Decode the base64 upload, checking the length before decoding anything (D-101)."""
    if len(content_base64) > MAX_BASE64_CHARS:
        raise too_large(f"the file is larger than {MAX_FILE_BYTES} bytes")
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise invalid("the upload is not valid base64") from exc
    if len(data) > MAX_FILE_BYTES:
        raise too_large(f"the file is larger than {MAX_FILE_BYTES} bytes")
    if not data:
        raise invalid("the file is empty")
    return data


class CellError(ValueError):
    """A cell does not hold the kind of value its column requires."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def cell_decimal(value: object) -> Decimal:
    """A numeric spreadsheet cell as an exact Decimal (D-105).

    Spreadsheets store numbers as binary doubles; `repr` of a float is the shortest decimal
    string that round-trips to the same double, i.e. the figure the broker printed
    (`-90.409999999999997` is read back as `-90.41`). Integers are exact already. Anything
    else (text, dates, booleans, empty cells, non-finite values) is a CellError.
    """
    if isinstance(value, bool):
        raise CellError("NOT_A_NUMBER")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CellError("NOT_A_NUMBER")
        try:
            return Decimal(repr(value))
        except InvalidOperation as exc:  # pragma: no cover - repr of a finite float parses
            raise CellError("NOT_A_NUMBER") from exc
    raise CellError("NOT_A_NUMBER")


def decimal_places(value: Decimal) -> int:
    """Significant decimals: trailing zeros do not count (`1.50` has 1, `100` has 0)."""
    exponent = value.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


# --- What every importer returns ------------------------------------------------------------


class Category(StrEnum):
    """Preview category of a candidate (`docs/import/directa.md` §7, `app-csv.md`)."""

    TO_IMPORT = "TO_IMPORT"  # selected by default
    TO_REVIEW = "TO_REVIEW"  # unconfirmed meaning (D-99): selectable, not selected
    SKIPPED_BY_CHOICE = "SKIPPED_BY_CHOICE"  # "do not move cash" (D-49): selectable
    AMBIGUOUS = "AMBIGUOUS"  # inconsistent rows (D-108): never imported
    NOT_RECOGNIZED = "NOT_RECOGNIZED"  # not verified (D-20): never imported
    INVALID = "INVALID"  # breaks a format or field rule (D-103): never imported
    # Already present among the user's transactions (D-38, Directa §6), found by the import
    # service: skipped, never imported again.
    DUPLICATE = "DUPLICATE"


SELECTABLE = frozenset({Category.TO_IMPORT, Category.TO_REVIEW, Category.SKIPPED_BY_CHOICE})


@dataclass(frozen=True, slots=True)
class Reason:
    """A stable code plus parameters (strings), translated by the UI."""

    code: str
    params: tuple[tuple[str, str], ...] = ()  # sorted (name, value) pairs: hashable

    def as_dict(self) -> dict[str, str]:
        return dict(self.params)


def reason(code: str, **params: object) -> Reason:
    return Reason(code, tuple(sorted((k, str(v)) for k, v in params.items())))


@dataclass(frozen=True, slots=True)
class AccountRef:
    """The account a CSV row names (D-38). Directa files have none: the user picks it."""

    name: str
    currency: str | None = None
    provider: str | None = None


@dataclass(frozen=True, slots=True)
class AssetRef:
    """How the file identifies an asset, and what it proposes for a new one (D-102, D-111).
    None fields are unknown: the user chooses them in the preview."""

    isin: str | None = None
    ticker: str | None = None
    name: str | None = None
    asset_type: AssetType | None = None
    quote_currency: str | None = None
    quote_unit: QuoteUnit | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Candidate:
    """One preview line: a transaction to import (selectable categories) or rejected rows.
    Amounts are in the account currency (domain-rules §2). A None `unit_price` on a BUY or
    SELL is derived by the service from the amounts and the chosen asset type."""

    key: str  # stable across preview and confirm of the same file (D-100)
    category: Category
    lines: tuple[int, ...]  # rows of the file, as the user sees them
    reasons: tuple[Reason, ...] = ()
    info: tuple[Reason, ...] = ()
    account: AccountRef | None = None
    asset: AssetRef | None = None
    id: UUID | None = None  # CSV only (D-38)
    type: TransactionType | None = None
    trade_date: date | None = None
    trade_time: time | None = None
    value_date: date | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    gross_amount: Decimal | None = None
    fees: Decimal | None = None
    accrued_interest_paid: Decimal | None = None
    accrued_interest_received: Decimal | None = None
    taxes: Decimal | None = None
    total_amount: Decimal | None = None
    split_ratio: SplitRatio | None = None
    affects_cash: bool = True
    external_ref: str | None = None
    notes: str | None = None
    metadata: Mapping[str, Any] | None = None
    account_expense: bool = False  # stamp duty / Tobin tax, grouped in the preview
    sequence: int | None = None  # 1.. in file order (D-70, Directa §8); selectable only

    @property
    def selectable(self) -> bool:
        return self.category in SELECTABLE

    @property
    def selected_by_default(self) -> bool:
        return self.category is Category.TO_IMPORT


@dataclass(frozen=True, slots=True)
class ParsedImport:
    source: str  # import batch source: "directa-xlsx" | "app-csv"
    candidates: tuple[Candidate, ...]
    account_code: str | None = None  # broker account code (Directa §4)
    notices: tuple[Reason, ...] = ()  # file-level, e.g. POSSIBLY_TRUNCATED
    first_date: date | None = None  # range of the trade dates (D-104)
    last_date: date | None = None


class Importer(Protocol):
    """A file format. `parse` reads untrusted bytes (limits of D-101) and returns every row
    in exactly one candidate; it raises ImportFileError when the file cannot be read."""

    source: str

    def parse(self, data: bytes, *, move_cash: bool = True) -> ParsedImport: ...


def without_cash(candidate: Candidate) -> Candidate:
    """The "do not move cash" option (D-49): trades and income keep their figures but do not
    move cash; deposits and withdrawals are skipped by default (selectable)."""
    if candidate.type in (TransactionType.BUY, TransactionType.SELL,
                          TransactionType.DIVIDEND, TransactionType.COUPON):  # fmt: skip
        return replace(candidate, affects_cash=False)
    if (
        candidate.type in (TransactionType.DEPOSIT, TransactionType.WITHDRAWAL)
        and candidate.category is Category.TO_IMPORT
    ):
        return replace(candidate, category=Category.SKIPPED_BY_CHOICE)
    return candidate


def derived_unit_price(candidate: Candidate, *, is_bond: bool) -> Decimal | None:
    """Unit price of a BUY/SELL from its amounts when the file gives none (Directa §3): per
    unit, or % of nominal for bonds; rounded once to 8 decimals (domain-rules §0)."""
    if candidate.unit_price is not None:
        return candidate.unit_price
    if candidate.type not in (TransactionType.BUY, TransactionType.SELL):
        return None
    if not candidate.quantity or candidate.gross_amount is None:
        return None
    gross = candidate.gross_amount * 100 if is_bond else candidate.gross_amount
    return div8(gross, candidate.quantity)
