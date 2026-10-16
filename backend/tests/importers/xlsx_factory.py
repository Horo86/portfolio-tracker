"""Build small `.xlsx` files from raw sheet XML, to control exactly what a cell stores
(e.g. the 17-digit doubles of real exports) and to craft hostile files. Invented data only."""

import io
import zipfile
from dataclasses import dataclass
from xml.sax.saxutils import escape

_CT = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
)
_WB_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
)
_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'

# A DTD whose entities expand a short string many times ("billion laughs" pattern).
ENTITY_PROLOG = (
    '<!DOCTYPE w [<!ENTITY a "AAAAAAAAAA">'
    '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
    '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]>'
)


@dataclass(frozen=True)
class Raw:
    """A numeric cell stored exactly as this text (e.g. "-12.339999999999999")."""

    text: str


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _cell(ref: str, value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Raw):
        return f'<c r="{ref}"><v>{value.text}</v></c>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
    if isinstance(value, int | float):
        return f'<c r="{ref}"><v>{value!r}</v></c>'
    return (
        f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'
    )


def sheet_xml(rows: list[list[object] | None], prolog: str = "") -> str:
    """Rows from row 1; None leaves the row out of the XML (a gap, like row 9 of Directa)."""
    body = []
    for number, row in enumerate(rows, start=1):
        if row is None:
            continue
        cells = "".join(_cell(f"{_col(i)}{number}", v) for i, v in enumerate(row))
        body.append(f'<row r="{number}">{cells}</row>')
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>{prolog}<worksheet {_NS}>'
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )


def workbook_xml(prolog: str = "", sheet_name: str = "Movimenti") -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>{prolog}<workbook {_NS} '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )


def build_xlsx(
    sheet: str, *, workbook: str | None = None, extra: dict[str, bytes] | None = None
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CT)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("xl/workbook.xml", workbook or workbook_xml())
        archive.writestr("xl/_rels/workbook.xml.rels", _WB_RELS)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def xlsx(rows: list[list[object] | None]) -> bytes:
    return build_xlsx(sheet_xml(rows))


# --- Directa layout (docs/import/directa.md §1) -------------------------------------------

DIRECTA_HEADER = [
    "Data operazione", "Data valuta", "Tipo operazione", "Ticker", "Isin", "Protocollo",
    "Descrizione", "Quantità", "Importo euro", "Importo Divisa", "Divisa", "Riferimento ordine",
]  # fmt: skip


def directa_metadata(account_line: str = "Conto : B0000 Test User") -> list[list[object] | None]:
    return [
        [account_line],
        ["Data estrazione : 10-10-2026 10:00:00"],
        [""],
        ["Tutti i movimenti ordinati per Data Operazione"],
        ["Dal : 01-01-2024"],
        ["al : 31-12-2024"],
        [""],
        ["Il file include i primi 3000 movimenti"],
        None,
        list(DIRECTA_HEADER),
    ]


def directa_row(
    movement: str,
    amount: object,
    *,
    day: str = "15-03-2024",
    value_day: str = "19-03-2024",
    ticker: str = "",
    isin: str = "",
    protocol: object = "",
    description: str = "",
    quantity: object = 0,
    foreign: object = 0,
    currency: str = "EUR",
    order_ref: str = "              ",
) -> list[object]:
    return [day, value_day, movement, ticker, isin, protocol, description, quantity, amount,
            foreign, currency, order_ref]  # fmt: skip


def directa_xlsx(rows: list[list[object]], account_line: str = "Conto : B0000 Test User") -> bytes:
    return xlsx(directa_metadata(account_line) + rows)
