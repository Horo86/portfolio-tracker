"""Hostile and malformed `.xlsx` uploads are refused before they can exhaust memory (D-101)."""

import io
import zipfile

import openpyxl
import pytest
from xlsx_factory import ENTITY_PROLOG, build_xlsx, sheet_xml, workbook_xml, xlsx

from portfolio_tracker.importers import xlsx as reader
from portfolio_tracker.importers.base import ImportFileError


def read_all(data: bytes, max_rows: int = 100) -> list:
    return list(reader.first_sheet_rows(data, max_rows=max_rows))


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
