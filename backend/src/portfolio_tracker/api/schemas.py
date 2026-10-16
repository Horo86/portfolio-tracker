"""Shared request/response types. Money and quantities are decimal strings (ADR-0004)."""

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PlainSerializer,
    StringConstraints,
    WithJsonSchema,
)

from portfolio_tracker.currencies import is_currency
from portfolio_tracker.db.models import User
from portfolio_tracker.db.types import canonical_decimal

# ASCII only, so the NOCASE uniqueness of the column is exact (M2 default).
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._-]{3,32}")
Username = Annotated[str, StringConstraints(pattern=rf"^{USERNAME_PATTERN.pattern}$")]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Language = Literal["it", "en"]
Theme = Literal["sobrio", "moderno", "ambra", "grafite"]
Role = Literal["INVESTOR", "ADMINISTRATOR"]


def _currency(code: str) -> str:
    if not is_currency(code):
        raise ValueError("not an ISO 4217 currency code")
    return code


Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$"), AfterValidator(_currency)]


# At most 15 integer digits (999 trillion): far above any real figure, and products of two
# inputs stay well inside the engine precision of 60 digits (D-98).
_DECIMAL = re.compile(r"-?[0-9]{1,15}(\.[0-9]{1,20})?")


def _parse_decimal(value: object) -> Decimal:
    """Money and quantities travel as decimal strings, never JSON numbers (ADR-0004): a
    JSON number would already have passed through a binary float in many clients.
    A finite Decimal is accepted too: it only comes from our own code, never from JSON."""
    if isinstance(value, Decimal) and value.is_finite():
        return value
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise ValueError(
            'must be a decimal string such as "1234.56" (at most 15 integer and 20 decimal digits)'
        )
    return Decimal(value)


DecimalStr = Annotated[
    Decimal,
    BeforeValidator(_parse_decimal),
    PlainSerializer(canonical_decimal, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": f"^{_DECIMAL.pattern}$", "examples": ["1234.56"]}),
]


def _isin(value: str) -> str:
    """ISO 6166: 2 letters, 9 alphanumerics, 1 check digit (Luhn over the digit expansion)."""
    digits = "".join(str(int(ch, 36)) for ch in value[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n // 10 + n % 10
    if (10 - total % 10) % 10 != int(value[-1]):
        raise ValueError("ISIN check digit is wrong")
    return value


Isin = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v),
    StringConstraints(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"),
    AfterValidator(_isin),
]


class Strict(BaseModel):
    """Request bodies reject unknown fields: a typo must not be silently ignored."""

    model_config = ConfigDict(extra="forbid")


class MeOut(BaseModel):
    id: UUID
    username: str
    display_name: str | None
    role: Role
    language: Language
    theme: Theme
    reporting_currency: str
    must_change_password: bool
    two_factor_enabled: bool  # always false until M6
    password_changed_at: datetime
    last_login_at: datetime | None

    @classmethod
    def of(cls, user: User) -> "MeOut":
        return cls(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            language=user.language,
            theme=user.theme,
            reporting_currency=user.reporting_currency,
            must_change_password=user.must_change_password,
            two_factor_enabled=False,
            password_changed_at=user.password_changed_at,
            last_login_at=user.last_login_at,
        )
