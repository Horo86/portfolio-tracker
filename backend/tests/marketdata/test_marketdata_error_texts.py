"""Every engine error code has a text in both languages of the price-sources page (D-196):
the page shows an engine's last error by its code, so a new `ErrorCode` without its texts
fails here, not in front of an administrator."""

import json
from pathlib import Path

import pytest

from portfolio_tracker.marketdata.base import ErrorCode

LOCALES = Path(__file__).parents[3] / "frontend/src/locales"


@pytest.mark.parametrize("language", ["it", "en"])
def test_every_error_code_has_a_text(language: str) -> None:
    texts = json.loads((LOCALES / f"priceSources.{language}.json").read_text(encoding="utf-8"))
    codes = texts["priceSources"]["page"]["errorCodes"]
    assert set(codes) == {code.value for code in ErrorCode}
    assert all(isinstance(text, str) and text.strip() for text in codes.values())
