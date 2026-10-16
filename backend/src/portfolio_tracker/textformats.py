"""Text formats shared by the API and the importers (pure: no web or database imports).

One definition each, so a decimal, a split ratio or an ISIN is accepted by a CSV import
exactly when the API would accept it.
"""

import re

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
