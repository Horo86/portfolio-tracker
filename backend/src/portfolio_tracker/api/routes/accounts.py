"""Investment accounts of the logged-in user (D-03, D-39, D-77; mockups 13 and 15).

Every query is filtered by the owner: another user's account answers 404, also to the
Administrator (D-33).
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session

from portfolio_tracker.api.deps import CurrentAuth, DbSession
from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.api.routes.auth import client_ip
from portfolio_tracker.api.schemas import Currency, Strict
from portfolio_tracker.db.models import Account
from portfolio_tracker.repositories import accounts, audit

router = APIRouter(prefix="/accounts", tags=["accounts"])

AccountName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Provider = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
ExternalCode = Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]


class AccountOut(BaseModel):
    id: UUID
    name: str
    provider: str | None
    currency: str
    external_code: str | None
    created_at: datetime
    transaction_count: int
    currency_locked: bool  # true while the account has a transaction (D-39)


class AccountIn(Strict):
    name: AccountName
    currency: Currency
    provider: Provider | None = None
    external_code: ExternalCode | None = None


class AccountPatch(Strict):
    name: AccountName | None = None
    currency: Currency | None = None
    provider: Provider | None = None
    external_code: ExternalCode | None = None


class AccountDeleteIn(Strict):
    """The user types the account name to confirm (D-77)."""

    confirm_name: str


class AccountDeletedOut(BaseModel):
    deleted_transactions: int


def account_out(db: Session, account: Account) -> AccountOut:
    count = accounts.count_transactions(db, account)
    return AccountOut(
        id=account.id,
        name=account.name,
        provider=account.provider,
        currency=account.currency,
        external_code=account.external_code,
        created_at=account.created_at,
        transaction_count=count,
        currency_locked=count > 0,
    )


def owned_account(db: Session, auth, account_id: UUID) -> Account:
    """The user's account, or 404 (also when it belongs to someone else)."""
    account = accounts.get_account(db, auth.user.id, account_id)
    if account is None:
        raise not_found("account")
    return account


def _fields(account: Account) -> dict:
    return {
        "name": account.name,
        "provider": account.provider,
        "currency": account.currency,
        "external_code": account.external_code,
    }


def _empty_to_none(value: str | None) -> str | None:
    return value or None


@router.get("")
def list_accounts(auth: CurrentAuth, db: DbSession) -> list[AccountOut]:
    return [account_out(db, a) for a in accounts.list_accounts(db, auth.user.id)]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_account(
    body: AccountIn, auth: CurrentAuth, request: Request, db: DbSession
) -> AccountOut:
    account = accounts.add_account(
        db,
        auth.user.id,
        name=body.name,
        currency=body.currency,
        provider=_empty_to_none(body.provider),
        external_code=_empty_to_none(body.external_code),
    )
    audit.record(
        db, "account.created", actor_id=auth.user.id, entity_type="account",
        entity_id=account.id, after=_fields(account), ip=client_ip(request),
    )  # fmt: skip
    return account_out(db, account)


@router.get("/{account_id}")
def get_account(account_id: UUID, auth: CurrentAuth, db: DbSession) -> AccountOut:
    return account_out(db, owned_account(db, auth, account_id))


@router.patch("/{account_id}")
def update_account(
    account_id: UUID, body: AccountPatch, auth: CurrentAuth, request: Request, db: DbSession
) -> AccountOut:
    """Name, provider and external code are always editable; the currency only while the
    account has no transaction (D-39). Only the fields sent are changed."""
    account = owned_account(db, auth, account_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise ApiError(422, "REQUEST_INVALID", "name cannot be null")
    if "currency" in changes:
        if changes["currency"] is None:
            raise ApiError(422, "REQUEST_INVALID", "currency cannot be null")
        if changes["currency"] != account.currency and accounts.has_transactions(db, account):
            raise ApiError(
                409,
                "CURRENCY_LOCKED",
                "the currency cannot change once the account has transactions",
            )
    before = _fields(account)
    for name in ("provider", "external_code"):
        if name in changes:
            changes[name] = _empty_to_none(changes[name])
    for name, value in changes.items():
        setattr(account, name, value)
    db.flush()
    audit.record(
        db, "account.updated", actor_id=auth.user.id, entity_type="account",
        entity_id=account.id, before=before, after=_fields(account), ip=client_ip(request),
    )  # fmt: skip
    return account_out(db, account)


@router.delete("/{account_id}")
def delete_account(
    account_id: UUID, body: AccountDeleteIn, auth: CurrentAuth, request: Request, db: DbSession
) -> AccountDeletedOut:
    """Deletes the account and all its transactions after the typed confirmation (D-77).
    Archiving is V2."""
    account = owned_account(db, auth, account_id)
    if body.confirm_name.strip() != account.name:
        raise ApiError(422, "CONFIRMATION_MISMATCH", "the typed name does not match the account")
    count = accounts.count_transactions(db, account)
    before = _fields(account) | {"transaction_count": count}
    accounts.delete_account(db, account)
    audit.record(
        db, "account.deleted", actor_id=auth.user.id, entity_type="account",
        entity_id=account_id, before=before, ip=client_ip(request),
    )  # fmt: skip
    return AccountDeletedOut(deleted_transactions=count)
