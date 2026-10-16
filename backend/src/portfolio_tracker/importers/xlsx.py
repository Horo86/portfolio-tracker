"""Bounded reading of the first sheet of an untrusted `.xlsx` file (D-101).

Checks run in order, each before the next step:
1. the zip directory: member count, encryption, and declared uncompressed sizes per part and
   in total (Python's zip reader never returns more bytes than a member declares, so the
   declared sizes bound the real ones);
2. a streaming pre-scan of every worksheet part with defusedxml, in near-constant memory: at
   most MAX_COLUMNS cells per row and `max_rows` rows. openpyxl builds a whole `<row>` before
   applying its column limit, so without this a single huge row of a small upload could take
   hundreds of MB;
3. openpyxl in read-only, values-only mode, XML parsed by defusedxml.
One file is parsed at a time in the process (a lock), so concurrent uploads cannot add up.
"""

import io
import posixpath
import threading
import warnings
import zipfile
from fnmatch import fnmatch

import openpyxl
from defusedxml.ElementTree import iterparse

from portfolio_tracker.importers.base import ImportFileError, invalid, too_large

# Sizes (D-101): real Directa exports take about 400-440 bytes per data row in the sheet part
# (shared strings), the synthetic fixture about 650 (inline strings). Budget 800 bytes per
# row: 10,000 rows = 8 MB, so 16 MB leaves a 2x margin. Shared strings: at most about 4
# distinct strings of about 40 bytes per row = 1.6 MB for 10,000 rows, capped at 4 MB.
# Every other part (styles, workbook, relationships, theme, properties) is a few KB.
MAX_ZIP_MEMBERS = 100
MAX_SHEET_BYTES = 16 * 1024 * 1024
MAX_SHARED_STRINGS_BYTES = 4 * 1024 * 1024
MAX_OTHER_PART_BYTES = 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 24 * 1024 * 1024
MAX_COLUMNS = 64  # more cells in one row: refused by the pre-scan
MAX_ROW_ELEMENTS = MAX_COLUMNS * 8  # a cell is <c> plus a few children (<v>, <is><t>, <f>)
MAX_OTHER_ELEMENTS = 10_000  # elements of a worksheet outside its rows (columns, merges...)

_SHEETS = "xl/worksheets/*.xml"
_PARSE_LOCK = threading.Lock()

# Spreadsheet quirks (missing styles, unknown extensions) are not our concern. Filtered once
# and only for openpyxl: changing the filters per call is not thread-safe.
warnings.filterwarnings("ignore", module=r"openpyxl(\.|$)")

Row = tuple[object, ...]


def _part_limit(name: str) -> int:
    if fnmatch(name, _SHEETS):
        return MAX_SHEET_BYTES
    if name == "xl/sharedStrings.xml":
        return MAX_SHARED_STRINGS_BYTES
    return MAX_OTHER_PART_BYTES


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
            if member.file_size > _part_limit(member.filename):
                raise too_large("an xlsx part is too large once decompressed")
            total += member.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise too_large("the xlsx is too large once decompressed")


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _worksheet_parts(archive: zipfile.ZipFile) -> list[str]:
    """Every part that may be read as a worksheet: the usual `xl/worksheets/*.xml` and every
    target of a worksheet relationship, wherever it is (openpyxl follows the relationships,
    so a sheet stored as e.g. `xl/data.xml` must be scanned too; final review of M3, F11)."""
    names = archive.namelist()
    parts = {n for n in names if fnmatch(n, _SHEETS)}
    for rels in (n for n in names if n.endswith(".rels")):
        source_dir = posixpath.dirname(posixpath.dirname(rels))  # "xl/_rels/x.rels" -> "xl"
        try:
            with archive.open(rels) as part:
                for _event, element in iterparse(part):
                    if _local(element.tag) != "Relationship":
                        continue
                    target = element.get("Target") or ""
                    if (element.get("Type") or "").endswith("/worksheet") and target:
                        path = (
                            target.lstrip("/")
                            if target.startswith("/")
                            else posixpath.normpath(posixpath.join(source_dir, target))
                        )
                        parts.add(path)
                    element.clear()
        except Exception as exc:  # any parse failure of an untrusted file is the same answer
            raise invalid("the xlsx cannot be read (invalid or forbidden XML)") from exc
    return sorted(parts & set(names))


def prescan_sheets(data: bytes, max_rows: int) -> None:
    """Stream every worksheet part, dropping each element when it ends, and refuse a row
    with more than MAX_COLUMNS cells or MAX_ROW_ELEMENTS elements, more than
    MAX_OTHER_ELEMENTS elements outside rows, or more than `max_rows` rows. openpyxl keeps
    every child of a row until the row ends, so these bound its memory too."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in _worksheet_parts(archive):
            rows = cells = in_row = outside = 0
            inside_row = False
            stack = []
            try:
                with archive.open(name) as part:
                    for event, element in iterparse(part, events=("start", "end")):
                        tag = _local(element.tag)
                        if event == "start":
                            stack.append(element)
                            inside_row = inside_row or tag == "row"
                            continue
                        stack.pop()
                        if tag == "row":
                            rows += 1
                            cells = in_row = 0
                            inside_row = False
                            if rows > max_rows:
                                raise too_large(f"the sheet has more than {max_rows} rows")
                        elif inside_row:
                            in_row += 1
                            cells += tag == "c"
                            if cells > MAX_COLUMNS or in_row > MAX_ROW_ELEMENTS:
                                raise too_large(f"a row has more than {MAX_COLUMNS} cells")
                        else:
                            outside += 1
                            if outside > MAX_OTHER_ELEMENTS:
                                raise too_large("the sheet has too many elements")
                        element.clear()
                        if stack:  # an ended element is the last child of its parent
                            del stack[-1][-1]
            except ImportFileError:
                raise
            except Exception as exc:  # any parse failure of an untrusted file is the same answer
                raise invalid("the xlsx cannot be read (invalid or forbidden XML)") from exc


def read_first_sheet(data: bytes, max_rows: int) -> list[tuple[int, Row]]:
    """(sheet row number, cell values) of the first sheet, at most `max_rows` rows (empty
    rows count). More rows → IMPORT_TOO_LARGE. Unreadable → IMPORT_FILE_INVALID."""
    if openpyxl.DEFUSEDXML is not True or openpyxl.LXML is not False:
        # openpyxl uses the standard parser when defusedxml is missing or disabled by the
        # OPENPYXL_DEFUSEDXML environment variable, and lxml when it is installed: the
        # hardening is verified for defusedxml only, so never read uploads otherwise.
        raise RuntimeError("defusedxml is not openpyxl's parser: refusing to read xlsx uploads")
    check_zip(data)
    with _PARSE_LOCK:
        prescan_sheets(data, max_rows)
        return _read(data, max_rows)


def _read(data: bytes, max_rows: int) -> list[tuple[int, Row]]:
    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(data), read_only=True, data_only=True, keep_links=False
        )
    except Exception as exc:  # any parse failure of an untrusted file is the same answer
        raise invalid("the xlsx cannot be read") from exc
    try:
        if not workbook.worksheets:
            raise invalid("the xlsx has no sheet")
        # Read-only sheets start at row 1 and yield empty rows for gaps, so the counter is
        # the sheet row number.
        rows = workbook.worksheets[0].iter_rows(max_col=MAX_COLUMNS, values_only=True)
        out = []
        for number, row in enumerate(rows, start=1):
            if number > max_rows:
                raise too_large(f"the sheet has more than {max_rows} rows")
            out.append((number, _trim(row)))
        return out
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
