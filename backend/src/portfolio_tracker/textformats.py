"""Text formats shared by the API and the importers (pure: no web or database imports).

One definition each, so a decimal, a split ratio or an ISIN is accepted by a CSV import
exactly when the API would accept it.
"""

import json
import re
from collections.abc import Mapping
from typing import Any

# At most 15 integer digits (999 trillion): far above any real figure, and products of two
# inputs stay well inside the engine precision of 60 digits (D-98).
DECIMAL = re.compile(r"-?[0-9]{1,15}(\.[0-9]{1,20})?")

# Stock split `new:old`, e.g. "2:1" (split) or "1:10" (reverse split).
SPLIT_RATIO = re.compile(r"([0-9]{1,12}(?:\.[0-9]{1,8})?):([0-9]{1,12}(?:\.[0-9]{1,8})?)")

ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")


def isin_check_digit_ok(value: str) -> bool:
    """ISO 6166: 2 letters, 9 alphanumerics, 1 check digit (Luhn over the digit expansion).
    `value` must already match ISIN."""
    digits = "".join(str(int(ch, 36)) for ch in value[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n // 10 + n % 10
    return (10 - total % 10) % 10 == int(value[-1])


def valid_isin(value: str) -> bool:
    return ISIN.fullmatch(value) is not None and isin_check_digit_ok(value)


# A transaction's metadata (e.g. the original broker type, D-13): at most this many characters
# of its canonical JSON text, the same measure for the API, the app CSV and imports (D-114).
MAX_METADATA_CHARS = 10_000


def metadata_text(value: Mapping[str, Any]) -> str:
    """The canonical JSON text of metadata, as the app CSV writes it: compact, keys sorted,
    UTF-8 (not escaped). Raises ValueError for NaN or Infinity, which are not JSON."""
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)  # fmt: skip


def metadata_fits(value: Mapping[str, Any]) -> bool:
    try:
        return len(metadata_text(value)) <= MAX_METADATA_CHARS
    except (ValueError, TypeError):
        return False
