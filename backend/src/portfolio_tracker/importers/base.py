"""Upload limits, file errors and the exact conversion of spreadsheet numbers (D-101, D-105)."""

import base64
import binascii
import math
from decimal import Decimal, InvalidOperation

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB (mockup 07)
MAX_BASE64_CHARS = 4 * math.ceil(MAX_FILE_BYTES / 3)


class ImportFileError(Exception):
    """The file cannot be read at all. `code` is stable (ADR-0004); `detail` is a developer
    hint that never contains file content (it may hold personal data)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


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
