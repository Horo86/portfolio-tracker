"""Read a Directa movements export into plain rows (`docs/import/directa.md` §1).

Only structure is interpreted here; grouping and mapping to transactions are in
`directa_mapping`. Personal data: of the `Conto :` line only the account code is kept, never
the holder name, and no cell content is ever logged or put in an error message.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from portfolio_tracker.importers.base import CellError, cell_decimal, decimal_places, invalid
from portfolio_tracker.importers.xlsx import Row, read_first_sheet

HEADER = (
    "Data operazione",
    "Data valuta",
    "Tipo operazione",
    "Ticker",
    "Isin",
    "Protocollo",
    "Descrizione",
    "Quantità",
    "Importo euro",
    "Importo Divisa",
    "Divisa",
    "Riferimento ordine",
)
MAX_HEADER_ROW = 50  # the header is on row 10 in every verified export
MAX_DATA_ROWS = 10_000  # D-101; Directa itself cuts exports at 3,000 movements
TRUNCATION_ROWS = 3000  # "Il file include i primi 3000 movimenti" (§1)

_DATE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")
_ACCOUNT = re.compile(r"Conto\s*:\s*([A-Za-z0-9]{1,32})(?:\s|$)")
_PERIOD = re.compile(r"(Dal|al)\s*:\s*(\d{2}-\d{2}-\d{4})\s*$")


@dataclass(frozen=True, slots=True)
class RowProblem:
    """Why a cell cannot be used: the row becomes ambiguous (D-105)."""

    column: str
    code: str  # DATE_INVALID, NOT_A_NUMBER, NOT_A_CODE, AMOUNT_PRECISION, QUANTITY_PRECISION


@dataclass(frozen=True, slots=True)
class DirectaRow:
    """One data row. Text cells are trimmed, empty text is None; numbers are exact Decimals
    (None only when the cell has a problem)."""

    line: int  # sheet row number, as the user sees it in Excel
    trade_date: date | None
    value_date: date | None
    movement: str  # `Tipo operazione`, matched exactly against the verified types
    ticker: str | None
    isin: str | None
    protocol: str | None
    description: str | None
    quantity: Decimal | None
    amount: Decimal | None  # `Importo euro`: cash movement, negative = cash out
    foreign_amount: Decimal | None
    currency: str | None
    order_ref: str | None
    problems: tuple[RowProblem, ...] = ()


@dataclass(frozen=True, slots=True)
class DirectaFile:
    account_code: str | None  # e.g. "B0000"; the holder name is dropped while reading
    period_from: date | None
    period_to: date | None
    rows: tuple[DirectaRow, ...] = field(default_factory=tuple)

    @property
    def possibly_truncated(self) -> bool:
        return len(self.rows) >= TRUNCATION_ROWS


def read_directa(data: bytes) -> DirectaFile:
    rows = iter(read_first_sheet(data, max_rows=MAX_HEADER_ROW + MAX_DATA_ROWS))
    account_code = period_from = period_to = None
    for number, cells in rows:
        first = _text(cells[0]) if cells else None
        if first == HEADER[0]:
            if tuple(_text(c) for c in cells[: len(HEADER)]) != HEADER or any(
                _text(c) for c in cells[len(HEADER) :]
            ):
                raise invalid("the Directa header does not have the verified columns")
            break
        if number >= MAX_HEADER_ROW:
            raise invalid("no Directa header ('Data operazione') in the first rows")
        if first is None:
            continue
        if match := _ACCOUNT.match(first):
            account_code = match.group(1)  # the rest of the line is the holder name: dropped
        elif match := _PERIOD.match(first):
            day = _date(match.group(2))
            if match.group(1) == "Dal":
                period_from = day
            else:
                period_to = day
    else:
        raise invalid("no Directa header ('Data operazione')")

    data_rows = [_row(number, cells) for number, cells in rows if any(map(_filled, cells))]
    if len(data_rows) > MAX_DATA_ROWS:  # pragma: no cover - the sheet row cap fires first
        raise invalid("too many rows")
    return DirectaFile(account_code, period_from, period_to, tuple(data_rows))


def _row(number: int, cells: Row) -> DirectaRow:
    cells = tuple(cells) + (None,) * (len(HEADER) - len(cells))
    by_name = dict(zip(HEADER, cells, strict=False))
    problems: list[RowProblem] = []

    def day(column: str) -> date | None:
        value = _text(by_name[column])
        parsed = _date(value) if value else None
        if parsed is None:
            problems.append(RowProblem(column, "DATE_INVALID"))
        return parsed

    def code(column: str) -> str | None:
        raw = by_name[column]
        value = _code(raw)
        if value is None and _filled(raw):
            problems.append(RowProblem(column, "NOT_A_CODE"))
        return value

    def number_cell(column: str, max_places: int | None = None, code: str = "") -> Decimal | None:
        try:
            value = cell_decimal(by_name[column])
        except CellError as exc:
            problems.append(RowProblem(column, exc.code))
            return None
        if max_places is not None and decimal_places(value) > max_places:
            problems.append(RowProblem(column, code))
            return None
        return value

    return DirectaRow(
        line=number,
        trade_date=day("Data operazione"),
        value_date=day("Data valuta"),
        movement=_text(by_name["Tipo operazione"]) or "",
        ticker=_text(by_name["Ticker"]),
        isin=_text(by_name["Isin"]),
        protocol=code("Protocollo"),
        description=_text(by_name["Descrizione"]),
        # D-68: quantities have at most 8 decimals; D-105: the euro amount at most 2.
        quantity=number_cell("Quantità", 8, "QUANTITY_PRECISION"),
        amount=number_cell("Importo euro", 2, "AMOUNT_PRECISION"),
        foreign_amount=number_cell("Importo Divisa"),
        currency=_text(by_name["Divisa"]),
        order_ref=code("Riferimento ordine"),
        problems=tuple(problems),
    )


def _filled(value: object) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _text(value: object) -> str | None:
    """Trimmed text of a text cell; None when empty. Numbers are not text."""
    if isinstance(value, str):
        return value.strip() or None
    return None


def _code(value: object) -> str | None:
    """Protocol / order reference: text (trimmed) or an integer cell; None when empty."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return None


def _date(value: str) -> date | None:
    match = _DATE.fullmatch(value)
    if match is None:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None
