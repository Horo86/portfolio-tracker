"""Upload decoding limits and the exact conversion of spreadsheet numbers (D-101, D-105)."""

import base64
import math
from decimal import Decimal

import pytest

from portfolio_tracker.importers import base
from portfolio_tracker.importers.base import (
    CellError,
    ImportFileError,
    cell_decimal,
    decimal_places,
    decode_upload,
)


def test_decode_upload_round_trip() -> None:
    assert decode_upload(base64.b64encode(b"hello").decode()) == b"hello"


def test_base64_length_is_checked_before_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("decoded an oversized upload")

    monkeypatch.setattr(base.base64, "b64decode", must_not_run)
    with pytest.raises(ImportFileError) as exc:
        decode_upload("A" * (base.MAX_BASE64_CHARS + 4))
    assert exc.value.code == "IMPORT_TOO_LARGE"


def test_limit_is_ten_megabytes() -> None:
    assert base.MAX_FILE_BYTES == 10 * 1024 * 1024
    assert math.ceil(base.MAX_FILE_BYTES / 3) * 4 == base.MAX_BASE64_CHARS


def test_decoded_size_is_checked_too(monkeypatch: pytest.MonkeyPatch) -> None:
    # 12 bytes encode to 16 characters: within the character limit, over the byte limit.
    monkeypatch.setattr(base, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(base, "MAX_BASE64_CHARS", 16)
    with pytest.raises(ImportFileError) as exc:
        decode_upload(base64.b64encode(b"x" * 12).decode())
    assert exc.value.code == "IMPORT_TOO_LARGE"


@pytest.mark.parametrize("text", ["not base64!", "QUJD\nREVG", "QUJ"])
def test_invalid_base64(text: str) -> None:
    with pytest.raises(ImportFileError) as exc:
        decode_upload(text)
    assert exc.value.code == "IMPORT_FILE_INVALID"


def test_empty_file() -> None:
    with pytest.raises(ImportFileError) as exc:
        decode_upload("")
    assert exc.value.code == "IMPORT_FILE_INVALID"


@pytest.mark.parametrize("printed", ["-12.34", "1001.5", "0.07", "4875.1", "97.35", "-0.9"])
def test_seventeen_digit_doubles_read_back_as_printed(printed: str) -> None:
    """Real exports store doubles with 17 significant digits; the shortest round-trip string
    is the figure the broker printed (invented values)."""
    stored = format(float(printed), ".17g")
    assert cell_decimal(float(stored)) == Decimal(printed)


def test_integers_are_exact() -> None:
    assert cell_decimal(5000) == Decimal(5000)
    assert cell_decimal(12345678901234567890) == Decimal("12345678901234567890")


@pytest.mark.parametrize("value", [True, False, None, "12.5", "", math.nan, math.inf, object()])
def test_non_numbers_are_rejected(value: object) -> None:
    with pytest.raises(CellError) as exc:
        cell_decimal(value)
    assert exc.value.code == "NOT_A_NUMBER"


@pytest.mark.parametrize(
    ("value", "places"),
    [("100", 0), ("1.50", 1), ("0.07", 2), ("1.005", 3), ("5E+1", 0), ("0", 0), ("-0.00000001", 8)],
)
def test_decimal_places(value: str, places: int) -> None:
    assert decimal_places(Decimal(value)) == places
