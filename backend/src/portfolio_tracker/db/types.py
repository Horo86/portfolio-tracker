"""Column types: exact decimals and UUIDs stored as TEXT (ADR-0011)."""

import uuid
from decimal import Decimal

from sqlalchemy import String, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


def canonical_decimal(value: Decimal) -> str:
    """Plain decimal string: no exponent, scale kept (`2.50` stays `2.50`), no `-0`."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise TypeError(f"not a finite Decimal: {value!r}")
    if value.is_zero():
        value = abs(value)
    return format(value, "f")


class DecimalText(TypeDecorator[Decimal]):
    """`decimal.Decimal` stored as canonical TEXT. Floats (and anything else) are rejected."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return canonical_decimal(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(value)


class UuidText(TypeDecorator[uuid.UUID]):
    """UUID stored as its 36-character lowercase TEXT form."""

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value: uuid.UUID | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            raise TypeError(f"not a UUID: {value!r}")
        return str(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> uuid.UUID | None:
        if value is None:
            return None
        return uuid.UUID(value)
