"""The app's own CSV format, version 1 (`docs/import/app-csv.md`): parse and write.

Import and export are one format (D-38): `write_csv` followed by `read_csv` gives back the
same records. Parsing checks the format only (columns, decimals, dates, codes); the field
rules of each type (domain-rules §2) are checked by the engine in the import service, with
the asset known. Rows are numbered like spreadsheet rows: the header is row 1.
"""

import csv
import io
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from portfolio_tracker import textformats
from portfolio_tracker.currencies import is_currency
from portfolio_tracker.engine.model import AssetType, QuoteUnit, SplitRatio, TransactionType
from portfolio_tracker.importers.base import (
    AccountRef,
    AssetRef,
    Candidate,
    Category,
    ImportFileError,
    ParsedImport,
    Reason,
    invalid,
    reason,
    too_large,
    without_cash,
)

SOURCE = "app-csv"
FORMAT_VERSION = "1"
COLUMNS = (
    "format_version", "id", "account", "account_currency", "account_provider",
    "trade_date", "trade_time", "value_date", "type",
    "isin", "ticker", "asset_name", "asset_type", "asset_quote_currency", "asset_quote_unit",
    "quantity", "unit_price", "gross_amount", "fees", "accrued_interest_paid",
    "accrued_interest_received", "taxes", "total_amount", "split_ratio", "affects_cash",
    "external_ref", "notes", "metadata",
)  # fmt: skip
REQUIRED = ("account", "trade_date", "type")
MAX_ROWS = 20_000  # D-101, header included
MAX_ACCOUNTS = 1000  # distinct `account` names per file (D-114), as the API's account choices
# Same limits as the API and the database columns.
MAX_LENGTH = {"account": 100, "account_provider": 100, "ticker": 32, "asset_name": 200,
              "external_ref": 64, "notes": 2000,
              # The raw cell, a parsing guard; the limit is on the canonical text (D-114).
              "metadata": 2 * textformats.MAX_METADATA_CHARS}  # fmt: skip
DECIMAL_COLUMNS = ("quantity", "unit_price", "gross_amount", "fees", "accrued_interest_paid",
                   "accrued_interest_received", "taxes", "total_amount")  # fmt: skip
# D-110: the text columns are protected against spreadsheet formulas, never numbers or
# dates.
FREE_TEXT = ("account", "account_provider", "ticker", "asset_name", "external_ref", "notes",
             "metadata")  # fmt: skip
_FORMULA_START = ("=", "+", "-", "@")
_CASH_FLAG_TYPES = frozenset(
    {TransactionType.BUY, TransactionType.SELL, TransactionType.DIVIDEND, TransactionType.COUPON}
)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME = re.compile(r"\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?")


@dataclass(frozen=True, slots=True, kw_only=True)
class CsvRecord:
    """One transaction row. Optional values not given are None (not 0, D-67)."""

    account: AccountRef
    type: TransactionType
    trade_date: date
    id: UUID | None = None
    trade_time: time | None = None
    value_date: date | None = None
    asset: AssetRef | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    gross_amount: Decimal | None = None
    fees: Decimal | None = None
    accrued_interest_paid: Decimal | None = None
    accrued_interest_received: Decimal | None = None
    taxes: Decimal | None = None
    total_amount: Decimal | None = None
    split_ratio: SplitRatio | None = None
    affects_cash: bool | None = None  # BUY/SELL/DIVIDEND/COUPON only; None = default true
    external_ref: str | None = None
    notes: str | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CsvRow:
    line: int
    record: CsvRecord | None  # None when the row has issues
    issues: tuple[Reason, ...] = ()
    id: UUID | None = None  # the row's `id` cell, read even when the row has other issues
    account: str | None = None  # likewise its `account` cell: the batch it is counted in


# --- Formula protection (D-110) ----------------------------------------------------------------


def _needs_protection(value: str) -> bool:
    """After any leading apostrophes, the text starts like a formula."""
    return value.lstrip("'")[:1] in _FORMULA_START


def protect(value: str) -> str:
    """Export: one more leading apostrophe before a value that starts like a formula after
    its own leading apostrophes (`=x` gets one, so does `''=x`), so the rule reverses."""
    return "'" + value if _needs_protection(value) else value


def unprotect(value: str) -> str:
    """Import: the exact inverse of `protect`. One leading apostrophe is removed only when
    what follows would itself be protected: `'=x` gives `=x`, `'hello` stays as it is."""
    if value[:1] == "'" and _needs_protection(value[1:]):
        return value[1:]
    return value


# --- Parsing --------------------------------------------------------------------------------


def read_csv(data: bytes) -> tuple[CsvRow, ...]:
    """Parse the file. Raises ImportFileError when the file itself cannot be used (encoding,
    header, version, size); row problems are returned as issues of that row."""
    try:
        text = data.decode("utf-8-sig")  # a leading byte-order mark is accepted
    except UnicodeDecodeError as exc:
        raise invalid("the CSV is not UTF-8 text") from exc
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
        if header is None:
            raise invalid("the CSV is empty")
        columns = _check_header(header)
        rows: list[CsvRow] = []
        for line, fields in enumerate(reader, start=2):
            if line > MAX_ROWS:
                raise too_large(f"the CSV has more than {MAX_ROWS} rows")
            if not any(f.strip() for f in fields):
                continue
            if len(fields) != len(columns):
                problem = reason("FIELD_COUNT", expected=len(columns), found=len(fields))
                rows.append(CsvRow(line, None, (problem,)))
                continue
            rows.append(_row(line, dict(zip(columns, fields, strict=True))))
    except csv.Error as exc:
        raise invalid("the CSV is malformed") from exc
    return _with_duplicate_ids(rows)


def _check_header(header: list[str]) -> list[str]:
    unknown = [c for c in header if c not in COLUMNS]
    if unknown:
        raise ImportFileError("IMPORT_FILE_INVALID", "unknown CSV column", column=unknown[0])
    repeated = [c for c, n in Counter(header).items() if n > 1]
    if repeated:
        raise ImportFileError("IMPORT_FILE_INVALID", "repeated CSV column", column=repeated[0])
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise ImportFileError("IMPORT_FILE_INVALID", "missing CSV column", column=missing[0])
    return header


def _row(line: int, cells: dict[str, str]) -> CsvRow:
    issues: list[Reason] = []

    def bad(column: str, problem: str) -> None:
        issues.append(reason("FIELD_INVALID", field=column, problem=problem))

    version = cells.get("format_version", FORMAT_VERSION).strip()
    if version != FORMAT_VERSION:  # D-103: refused for the whole file
        raise ImportFileError(
            "IMPORT_FORMAT_VERSION", "unsupported CSV format version", line=str(line)
        )

    def text(column: str, *, strip: bool = True) -> str | None:
        value = cells.get(column, "")
        if column in FREE_TEXT:
            value = unprotect(value)
        if strip:
            value = value.strip()
        if not value:
            return None
        if len(value) > MAX_LENGTH.get(column, 64):
            bad(column, "TOO_LONG")
            return None
        return value

    def parsed[V](column: str, parse, problem: str) -> V | None:
        value = text(column)
        if value is None:
            return None
        try:
            return parse(value)
        except (ValueError, TypeError):
            bad(column, problem)
            return None

    def required[V](column: str, value: V | None) -> V | None:
        if value is None and not any(i.as_dict().get("field") == column for i in issues):
            bad(column, "MISSING")
        return value

    def _metadata() -> dict[str, Any] | None:
        try:
            return parsed("metadata", _json_object, "NOT_A_JSON_OBJECT")
        except MetadataTooLong:
            bad("metadata", "TOO_LONG")
            return None

    account_name = required("account", text("account"))
    record_fields: dict[str, Any] = {
        "id": parsed("id", UUID, "NOT_A_UUID"),
        "trade_date": required("trade_date", parsed("trade_date", _date, "NOT_A_DATE")),
        "trade_time": parsed("trade_time", _time, "NOT_A_TIME"),
        "value_date": parsed("value_date", _date, "NOT_A_DATE"),
        "type": required("type", parsed("type", TransactionType, "UNKNOWN_TYPE")),
        "split_ratio": parsed("split_ratio", _split, "NOT_A_SPLIT_RATIO"),
        "affects_cash": parsed("affects_cash", _bool, "NOT_A_BOOLEAN"),
        "external_ref": text("external_ref"),
        "notes": text("notes", strip=False),
        "metadata": _metadata(),
    }
    for column in DECIMAL_COLUMNS:
        record_fields[column] = parsed(column, _decimal, "NOT_A_DECIMAL")
    account = AccountRef(
        name=account_name or "",
        currency=parsed("account_currency", _currency, "NOT_A_CURRENCY"),
        provider=text("account_provider"),
    )
    asset = AssetRef(
        isin=parsed("isin", _isin, "NOT_AN_ISIN"),
        ticker=text("ticker"),
        name=text("asset_name"),
        asset_type=parsed("asset_type", AssetType, "UNKNOWN_ASSET_TYPE"),
        quote_currency=parsed("asset_quote_currency", _currency, "NOT_A_CURRENCY"),
        quote_unit=parsed("asset_quote_unit", QuoteUnit, "UNKNOWN_QUOTE_UNIT"),
    )
    tx_type, flag = record_fields["type"], record_fields["affects_cash"]
    if tx_type is not None and tx_type not in _CASH_FLAG_TYPES and flag is not None:
        # The flag exists only for BUY/SELL/DIVIDEND/COUPON (D-62, domain-rules §3).
        bad("affects_cash", "NOT_ALLOWED_FOR_TYPE")
    if issues:
        return CsvRow(line, None, tuple(issues), id=record_fields["id"], account=account_name)
    has_asset = asset != AssetRef()
    record = CsvRecord(account=account, asset=asset if has_asset else None, **record_fields)
    return CsvRow(line, record, id=record.id, account=account_name)


def _with_duplicate_ids(rows: list[CsvRow]) -> tuple[CsvRow, ...]:
    """The same `id` twice in one file: every such row is refused, naming the others, also
    when one of them has other issues (fixing it later must not turn it into a duplicate)."""
    lines_of: dict[UUID, list[int]] = {}
    for row in rows:
        if row.id is not None:
            lines_of.setdefault(row.id, []).append(row.line)
    out = []
    for row in rows:
        lines = lines_of.get(row.id) if row.id is not None else None
        if lines and len(lines) > 1:
            other = ",".join(str(n) for n in lines if n != row.line)
            issue = reason("DUPLICATE_ID", lines=other)
            row = CsvRow(row.line, None, (*row.issues, issue), id=row.id, account=row.account)
        out.append(row)
    return tuple(out)


def _date(value: str) -> date:
    if not _DATE.fullmatch(value):
        raise ValueError(value)
    return date.fromisoformat(value)


def _time(value: str) -> time:
    if not _TIME.fullmatch(value):
        raise ValueError(value)
    return time.fromisoformat(value)


def _decimal(value: str) -> Decimal:
    if not textformats.DECIMAL.fullmatch(value):  # D-98, as in the API
        raise ValueError(value)
    return Decimal(value)


def _split(value: str) -> SplitRatio:
    match = textformats.SPLIT_RATIO.fullmatch(value)
    if match is None:
        raise ValueError(value)
    return SplitRatio(new=Decimal(match.group(1)), old=Decimal(match.group(2)))


def _bool(value: str) -> bool:
    if value not in ("true", "false"):
        raise ValueError(value)
    return value == "true"


def _currency(value: str) -> str:
    if not is_currency(value):
        raise ValueError(value)
    return value


def _isin(value: str) -> str:
    value = value.upper()
    if not textformats.valid_isin(value):
        raise ValueError(value)
    return value


def _no_constant(name: str) -> None:
    raise ValueError(name)


def _json_object(value: str) -> dict[str, Any]:
    result = json.loads(value, parse_constant=_no_constant)  # NaN/Infinity are not JSON
    if not isinstance(result, dict):
        raise ValueError("not an object")
    if not textformats.metadata_fits(result):
        raise MetadataTooLong
    return result


class MetadataTooLong(Exception):  # not a ValueError: `parsed` must not catch it
    """Valid JSON, but its canonical text is over the limit (D-114)."""


# --- Importer ------------------------------------------------------------------------------


class AppCsvImporter:
    source = SOURCE

    def parse(self, data: bytes, *, move_cash: bool = True) -> ParsedImport:
        rows = read_csv(data)
        candidates: list[Candidate] = []
        sequence = 0
        for row in rows:
            if row.record is None:
                account = AccountRef(name=row.account) if row.account else None
                candidates.append(
                    Candidate(key=f"line:{row.line}", category=Category.INVALID,
                              lines=(row.line,), reasons=row.issues, account=account)
                )  # fmt: skip
                continue
            sequence += 1  # D-70: in the order of the file rows
            candidates.append(_candidate(row.line, row.record, sequence))
        names = {c.account.name for c in candidates if c.account is not None}
        if len(names) > MAX_ACCOUNTS:
            raise too_large(f"the CSV names more than {MAX_ACCOUNTS} accounts")
        if not move_cash:
            candidates = [without_cash(c) for c in candidates]
        days = [c.trade_date for c in candidates if c.trade_date is not None]
        return ParsedImport(
            source=SOURCE,
            candidates=tuple(candidates),
            first_date=min(days, default=None),
            last_date=max(days, default=None),
        )


def _candidate(line: int, record: CsvRecord, sequence: int) -> Candidate:
    fields = {
        name: getattr(record, name)
        for name in CsvRecord.__dataclass_fields__
        if name not in ("affects_cash",)
    }
    return Candidate(
        key=f"line:{line}",
        category=Category.TO_IMPORT,
        lines=(line,),
        sequence=sequence,
        affects_cash=True if record.affects_cash is None else record.affects_cash,
        **fields,
    )


# --- Writing --------------------------------------------------------------------------------


def write_csv(records: Iterable[CsvRecord]) -> str:
    """Every column, `format_version` first; empty cells where not applicable (app-csv.md)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for record in records:
        writer.writerow(_cells(record))
    return out.getvalue()


def _cells(record: CsvRecord) -> list[str]:
    asset = record.asset or AssetRef()
    split = record.split_ratio
    affects_cash = record.affects_cash
    if record.type not in _CASH_FLAG_TYPES:
        affects_cash = None  # the flag exists only for trades and income (domain-rules §3)
    values: dict[str, str] = {
        "format_version": FORMAT_VERSION,
        "id": str(record.id) if record.id else "",
        "account": protect(record.account.name),
        "account_currency": record.account.currency or "",
        "account_provider": protect(record.account.provider or ""),
        "trade_date": record.trade_date.isoformat(),
        "trade_time": _time_text(record.trade_time),
        "value_date": record.value_date.isoformat() if record.value_date else "",
        "type": record.type.value,
        "isin": asset.isin or "",
        "ticker": protect(asset.ticker or ""),
        "asset_name": protect(asset.name or ""),
        "asset_type": asset.asset_type.value if asset.asset_type else "",
        "asset_quote_currency": asset.quote_currency or "",
        "asset_quote_unit": asset.quote_unit.value if asset.quote_unit else "",
        "split_ratio": f"{_number(split.new)}:{_number(split.old)}" if split else "",
        "affects_cash": "" if affects_cash is None else str(affects_cash).lower(),
        "external_ref": protect(record.external_ref or ""),
        "notes": protect(record.notes or ""),
        "metadata": protect(_json_text(record.metadata)),
    }
    for column in DECIMAL_COLUMNS:
        value = getattr(record, column)
        values[column] = "" if value is None else _number(value)
    return [values[c] for c in COLUMNS]


def _number(value: Decimal) -> str:
    """Plain notation with the stored digits (never an exponent): `1000.00` stays `1000.00`."""
    return format(value, "f")


def _time_text(value: time | None) -> str:
    if value is None:
        return ""
    if value.second == 0 and value.microsecond == 0:
        return value.strftime("%H:%M")
    return value.isoformat()


def _json_text(value: Mapping[str, Any] | None) -> str:
    if value is None:
        return ""
    return textformats.metadata_text(value)
