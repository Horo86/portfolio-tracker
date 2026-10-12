import subprocess
import sys
from decimal import Decimal

import pytest

from portfolio_tracker.engine.decimals import (
    EngineDecimalError,
    engine_context,
    mul_div8,
    q8,
    ratio8,
    to_decimal,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("33.333333335"), Decimal("33.33333334")),  # half-even: 3 is odd -> up
        (Decimal("33.333333325"), Decimal("33.33333332")),  # half-even: 2 is even -> stays
        (Decimal("-2.444444445"), Decimal("-2.44444444")),
        (Decimal("100"), Decimal("100.00000000")),
    ],
)
def test_q8_rounds_half_even_to_eight_decimals(value: Decimal, expected: Decimal) -> None:
    assert q8(value) == expected
    assert q8(value).as_tuple().exponent == -8


def test_mul_div8_multiplies_before_dividing_and_rounds_once() -> None:
    # E8: cost removed = 100.00 x 1 / 3
    assert mul_div8(Decimal("100.00"), Decimal("1"), Decimal("3")) == Decimal("33.33333333")
    # E8 second sale: 66.66666667 x 2 / 2 is exact
    assert mul_div8(Decimal("66.66666667"), Decimal("2"), Decimal("2")) == Decimal("66.66666667")


def test_twenty_digit_values_stay_exact() -> None:
    big = Decimal("12345678901234567890.12345678")
    with engine_context():
        assert big + big - big == big
    assert mul_div8(big, Decimal("3"), Decimal("3")) == big


def test_ratio8_is_none_for_zero_base() -> None:
    assert ratio8(Decimal("60.00"), Decimal("600.00")) == Decimal("0.10000000")
    assert ratio8(Decimal("5"), Decimal("0")) is None


@pytest.mark.parametrize("value", ["1.5", 7, Decimal("0.1"), "-2.44", "0"])
def test_to_decimal_accepts_exact_inputs(value: str | int | Decimal) -> None:
    assert to_decimal(value) == Decimal(str(value))


@pytest.mark.parametrize("value", [0.1, True, "abc", "NaN", "Infinity", Decimal("NaN"), None])
def test_to_decimal_rejects_floats_and_non_finite(value: object) -> None:
    with pytest.raises(EngineDecimalError):
        to_decimal(value)  # type: ignore[arg-type]


def test_engine_package_has_no_database_or_web_imports() -> None:
    code = (
        "import sys, portfolio_tracker.engine\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "{'sqlalchemy', 'fastapi', 'starlette', 'pydantic', 'alembic', 'httpx2'}]\n"
        "assert not bad, bad\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
