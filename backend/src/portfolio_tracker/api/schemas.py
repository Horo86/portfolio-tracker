"""Shared request/response types. Money and quantities are decimal strings (ADR-0004)."""

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from portfolio_tracker.currencies import is_currency
from portfolio_tracker.db.models import User

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
