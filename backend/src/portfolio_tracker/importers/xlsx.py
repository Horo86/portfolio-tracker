"""Bounded reading of the first sheet of an untrusted `.xlsx` file (D-101).

Checks run in order, each before the next step: the zip directory (member count, declared
uncompressed sizes, encryption), then openpyxl in read-only, values-only mode with XML
parsed by defusedxml, read row by row and stopped at the row cap. Python's zip reader never
returns more bytes than a member declares, so the declared sizes bound the real ones.
"""

import io
import warnings
import zipfile
from collections.abc import Iterator

import openpyxl

from portfolio_tracker.importers.base import ImportFileError, invalid, too_large

MAX_ZIP_MEMBERS = 100
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_COLUMNS = 64  # cells beyond this column are not read

Row = tuple[object, ...]


def check_zip(data: bytes) -> None:
    """Refuse what is not a plain zip within the limits, before any XML is parsed."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise invalid("not an xlsx file (not a zip archive)") from exc
    with archive:
        members = archive.infolist()
        if len(members) > MAX_ZIP_MEMBERS:
            raise too_large(f"the xlsx has more than {MAX_ZIP_MEMBERS} parts")
        total = 0
        for member in members:
            if member.flag_bits & 0x1:
                raise invalid("the xlsx is encrypted")
            if member.file_size > MAX_MEMBER_BYTES:
                raise too_large("an xlsx part is too large once decompressed")
            total += member.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise too_large("the xlsx is too large once decompressed")


def first_sheet_rows(data: bytes, max_rows: int) -> Iterator[tuple[int, Row]]:
    """Yield (sheet row number, cell values) of the first sheet, at most `max_rows` rows
    (empty rows count). More rows → IMPORT_TOO_LARGE. Unreadable → IMPORT_FILE_INVALID."""
    if openpyxl.DEFUSEDXML is not True:
        # openpyxl falls back to the standard parser when defusedxml is missing or the
        # OPENPYXL_DEFUSEDXML environment variable disables it: never read uploads then.
        raise RuntimeError("defusedxml is not active in openpyxl: refusing to read xlsx uploads")
    check_zip(data)
    with warnings.catch_warnings():
        # Spreadsheet quirks (missing styles, unknown extensions) are not our concern.
        warnings.simplefilter("ignore")
        try:
            workbook = openpyxl.load_workbook(
                io.BytesIO(data), read_only=True, data_only=True, keep_links=False
            )
        except Exception as exc:  # any parse failure of an untrusted file is the same answer
            raise invalid("the xlsx cannot be read") from exc
        try:
            if not workbook.worksheets:
                raise invalid("the xlsx has no sheet")
            # Read-only sheets start at row 1 and yield empty rows for gaps, so the counter
            # is the sheet row number.
            rows = workbook.worksheets[0].iter_rows(max_col=MAX_COLUMNS, values_only=True)
            try:
                for number, row in enumerate(rows, start=1):
                    if number > max_rows:
                        raise too_large(f"the sheet has more than {max_rows} rows")
                    yield number, _trim(row)
            except ImportFileError:
                raise
            except Exception as exc:  # any parse failure of an untrusted file is the same answer
                raise invalid("the xlsx cannot be read (invalid or forbidden XML)") from exc
        finally:
            workbook.close()


def _trim(row: tuple[object, ...]) -> Row:
    """Drop trailing empty cells (`max_col` pads every row to MAX_COLUMNS)."""
    end = len(row)
    while end and row[end - 1] is None:
        end -= 1
    return tuple(row[:end])
