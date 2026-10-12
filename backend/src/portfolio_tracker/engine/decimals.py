"""Exact decimal helpers: the only rounding rule of the engine (domain-rules §0).

Every derived division is rounded once, on the result, to 8 decimals half-even.
Sums and differences are exact. Floats are never accepted (D-32, ADR-0011).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

EIGHT_PLACES = Decimal("0.00000001")

# Enough digits for 20-digit amounts times 8+ decimal quantities without loss.
_CONTEXT = Context(
    prec=60,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


class EngineDecimalError(ValueError):
    """A value is not an exact, finite decimal."""


@contextmanager
def engine_context() -> Iterator[None]:
    """Run arithmetic with the engine precision instead of Python's default 28 digits."""
    with localcontext(_CONTEXT):
        yield


def to_decimal(value: Decimal | int | str) -> Decimal:
    """Convert an exact input to Decimal; reject float, bool, None and non-finite values."""
    if isinstance(value, bool) or not isinstance(value, Decimal | int | str):
        raise EngineDecimalError(f"not an exact decimal: {value!r}")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise EngineDecimalError(f"not a decimal number: {value!r}") from exc
    if not result.is_finite():
        raise EngineDecimalError(f"not a finite decimal: {value!r}")
    return result


def q8(value: Decimal) -> Decimal:
    """Round to 8 decimals, half-even."""
    with engine_context():
        return value.quantize(EIGHT_PLACES, rounding=ROUND_HALF_EVEN)


def mul_div8(value: Decimal, numerator: Decimal, denominator: Decimal) -> Decimal:
    """value x numerator / denominator, rounded once to 8 decimals (e.g. cost removed, D-40)."""
    with engine_context():
        return q8(value * numerator / denominator)


def div8(numerator: Decimal, denominator: Decimal) -> Decimal:
    """numerator / denominator, rounded once to 8 decimals."""
    with engine_context():
        return q8(numerator / denominator)


def ratio8(value: Decimal, base: Decimal) -> Decimal | None:
    """Percentage as a ratio (0.10000000 = +10%); None when the base is zero (D-57)."""
    if base == 0:
        return None
    return div8(value, base)
