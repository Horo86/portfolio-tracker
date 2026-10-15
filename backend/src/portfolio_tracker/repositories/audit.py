"""Append-only audit log (domain-model.md): who, when, what, before/after."""

from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from portfolio_tracker.db.models import AuditEntry
from portfolio_tracker.db.types import canonical_decimal


def jsonable(value: Any) -> Any:
    """JSON-safe copy: decimals as exact strings (never floats), ids and dates as text."""
    if isinstance(value, Decimal):
        return canonical_decimal(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value


def record(
    session: Session,
    action: str,
    *,
    actor_id: UUID | None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    before: Any = None,
    after: Any = None,
    ip: str | None = None,
) -> AuditEntry:
    entry = AuditEntry(
        action=action,
        actor_user_id=actor_id,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
        before=jsonable(before),
        after=jsonable(after),
        ip=ip,
    )
    session.add(entry)
    session.flush()
    return entry
