"""Hostile and malformed `.xlsx` uploads are refused before they can exhaust memory (D-101)."""

import io
import tracemalloc
import zipfile

import openpyxl
import pytest
from xlsx_factory import (
    ENTITY_PROLOG,
    Raw,
    build_xlsx,
    directa_metadata,
    directa_row,
    sheet_xml,
    workbook_xml,
    xlsx,
)

from portfolio_tracker.importers import xlsx as reader
from portfolio_tracker.importers.base import ImportFileError


def read_all(data: bytes, max_rows: int = 100) -> list:
    return reader.read_first_sheet(data, max_rows=max_rows)


def code_of(data: bytes, max_rows: int = 100) -> str:
    with pytest.raises(ImportFileError) as exc:
        read_all(data, max_rows)
    return exc.value.code


def refused_by_defusedxml(data: bytes) -> bool:
    """The file is refused as invalid, and the root cause is defusedxml (not another error)."""
    with pytest.raises(ImportFileError) as exc:
        read_all(data)
    assert exc.value.code == "IMPORT_FILE_INVALID"
    error: BaseException | None = exc.value
    while error is not None:
        if type(error).__module__.startswith("defusedxml"):
            return True
        error = error.__cause__ or error.__context__
    return False


def test_defusedxml_is_active_in_openpyxl() -> None:
    assert openpyxl.DEFUSEDXML is True


def test_refuses_to_read_without_defusedxml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openpyxl, "DEFUSEDXML", False)
    with pytest.raises(RuntimeError, match="defusedxml"):
        read_all(xlsx([["a"]]))


def test_rows_with_gaps_keep_their_sheet_numbers() -> None:
    rows = read_all(xlsx([["a", 1], None, ["b", 2.5]]))
    assert rows == [(1, ("a", 1)), (2, ()), (3, ("b", 2.5))]


def test_not_a_zip() -> None:
    assert code_of(b"Data operazione;Data valuta\n") == "IMPORT_FILE_INVALID"


def test_zip_without_a_workbook() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "hi")
    assert code_of(buffer.getvalue()) == "IMPORT_FILE_INVALID"


def test_invalid_sheet_xml() -> None:
    assert code_of(build_xlsx("<worksheet><sheetData><row>")) == "IMPORT_FILE_INVALID"


def test_entity_expansion_in_the_sheet_is_refused() -> None:
    data = build_xlsx(sheet_xml([["&c;"]], prolog=ENTITY_PROLOG).replace("&amp;c;", "&c;"))
    assert refused_by_defusedxml(data)


def test_entity_expansion_in_the_workbook_is_refused() -> None:
    data = build_xlsx(sheet_xml([["a"]]), workbook=workbook_xml(prolog=ENTITY_PROLOG))
    assert refused_by_defusedxml(data)


def test_external_entity_is_refused() -> None:
    prolog = '<!DOCTYPE w [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    data = build_xlsx(sheet_xml([["&x;"]], prolog=prolog).replace("&amp;x;", "&x;"))
    assert refused_by_defusedxml(data)


def test_row_cap_stops_reading() -> None:
    assert code_of(xlsx([["r"]] * 6), max_rows=5) == "IMPORT_TOO_LARGE"
    assert len(read_all(xlsx([["r"]] * 5), max_rows=5)) == 5


def test_too_many_zip_members(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "MAX_ZIP_MEMBERS", 6)
    data = build_xlsx(sheet_xml([["a"]]), extra={"a.xml": b"<a/>", "b.xml": b"<b/>"})
    assert code_of(data) == "IMPORT_TOO_LARGE"


def test_zip_bomb_member_is_refused_from_its_declared_size() -> None:
    """33 MB of zeros compress to a few KB; the declared size is checked before reading."""
    data = build_xlsx(sheet_xml([["a"]]), extra={"xl/media/bomb.bin": b"\0" * (33 * 1024 * 1024)})
    assert len(data) < 200_000
    assert code_of(data) == "IMPORT_TOO_LARGE"


def test_total_uncompressed_size_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader, "MAX_UNCOMPRESSED_BYTES", 5_000)
    parts = {f"xl/media/p{i}.bin": b"\0" * 2_000 for i in range(3)}
    assert code_of(build_xlsx(sheet_xml([["a"]]), extra=parts)) == "IMPORT_TOO_LARGE"


def test_encrypted_zip_member_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("xl/workbook.xml")
        info.flag_bits |= 0x1
        archive.writestr(info, b"<workbook/>")
    assert code_of(buffer.getvalue()) == "IMPORT_FILE_INVALID"


# --- Memory bounds of small hostile uploads (Opus review M3, finding 1) ----------------------


def peak_mib_while_refused(data: bytes) -> float:
    """Peak Python memory (MiB) while the upload is read; it must be refused as too large."""
    tracemalloc.start()
    try:
        assert code_of(data, max_rows=reader_max_rows()) == "IMPORT_TOO_LARGE"
        return tracemalloc.get_traced_memory()[1] / 2**20
    finally:
        tracemalloc.stop()


def reader_max_rows() -> int:
    from portfolio_tracker.importers.directa_reader import MAX_DATA_ROWS, MAX_HEADER_ROW

    return MAX_HEADER_ROW + MAX_DATA_ROWS


def wide_sheet(cell: str, count: int) -> str:
    return sheet_xml([]).replace("<sheetData>", f'<sheetData><row r="1">{cell * count}</row>')


def test_one_wide_row_of_cells_is_refused_in_bounded_memory() -> None:
    data = build_xlsx(wide_sheet("<c><v>1</v></c>", 600_000))  # about 9 MB of XML
    assert len(data) < 100_000
    assert peak_mib_while_refused(data) < 20


def test_one_row_full_of_other_elements_is_refused_in_bounded_memory() -> None:
    data = build_xlsx(wide_sheet("<x/>", 3_000_000))  # 12 MB of tiny elements
    assert peak_mib_while_refused(data) < 20


def test_too_many_elements_outside_rows() -> None:
    sheet = sheet_xml([["a"]]).replace("</sheetData>", "</sheetData>" + "<x/>" * 20_000)
    assert code_of(build_xlsx(sheet)) == "IMPORT_TOO_LARGE"


@pytest.mark.parametrize(
    ("part", "size"),
    [
        ("xl/worksheets/sheet2.xml", reader.MAX_SHEET_BYTES + 1),
        ("xl/sharedStrings.xml", reader.MAX_SHARED_STRINGS_BYTES + 1),
        ("xl/styles.xml", reader.MAX_OTHER_PART_BYTES + 1),
    ],
)
def test_per_part_caps(part: str, size: int) -> None:
    data = build_xlsx(sheet_xml([["a"]]), extra={part: b" " * size})
    assert code_of(data) == "IMPORT_TOO_LARGE"


def test_a_legitimate_10000_row_export_fits_with_a_2x_margin() -> None:
    """D-101: the worst realistic layout (inline strings, long descriptions) of a 10,000-row
    Directa export uses at most half of the sheet cap."""
    row = directa_row(
        "Ritenuta dividendi italia", Raw("-123456.78"), ticker="M.123456", isin="IT0TEST00031",
        protocol=123456789, description="TEST DESCRIPTION LONG 30 CHARS",
        quantity=Raw("12345.12345678"), order_ref="T0000000000001",
    )  # fmt: skip
    sheet = sheet_xml(directa_metadata() + [row] * 10_000)
    assert len(sheet.encode()) * 2 <= reader.MAX_SHEET_BYTES


# --- Final review of M3: sheets outside xl/worksheets/ (F11) ----------------------------------


def relocated(sheet: str, target: str, path: str) -> bytes:
    """An xlsx whose only sheet is stored at `path`, named by the workbook relationship."""
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'relationships/worksheet" Target="{target}"/></Relationships>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(build_xlsx(sheet_xml([["x"]])))) as source, \
            zipfile.ZipFile(buffer, "w") as out:  # fmt: skip
        for item in source.infolist():
            if item.filename == "xl/worksheets/sheet1.xml":
                continue
            content = rels if item.filename == "xl/_rels/workbook.xml.rels" else source.read(item)
            out.writestr(item.filename, content)
        out.writestr(path, sheet)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("target", "path"),
    [("data.xml", "xl/data.xml"), ("/xl/sharedStrings.xml", "xl/sharedStrings.xml"),
     ("../other/s.xml", "other/s.xml")],
)  # fmt: skip
def test_a_sheet_anywhere_is_prescanned(target: str, path: str) -> None:
    wide = sheet_xml([[f"c{n}" for n in range(reader.MAX_COLUMNS + 6)]])
    assert code_of(relocated(wide, target, path)) == "IMPORT_TOO_LARGE"
    assert read_all(relocated(sheet_xml([["fine"]]), target, path)) == [(1, ("fine",))]
