"""Ledger changes validated on the final state only (domain-rules §4, D-06, D-06b).

Every operation is pure: it returns the would-be ledger and whether it is accepted.
A rejected change leaves the original transactions untouched. Warnings never reject.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from portfolio_tracker.engine.ledger import ConflictCode, Ledger, QuantityConflictError, replay
from portfolio_tracker.engine.model import Asset, Transaction
from portfolio_tracker.engine.validation import FieldIssue, validate_transaction


@dataclass(frozen=True, slots=True)
class Conflict:
    """First transaction that would leave an invalid quantity (below zero, or split to zero)."""

    transaction_id: UUID
    asset_id: UUID
    quantity: Decimal
    code: ConflictCode


@dataclass(frozen=True, slots=True)
class Outcome:
    accepted: bool
    transactions: tuple[Transaction, ...]  # the new ledger if accepted, else the original one
    ledger: Ledger | None = None  # replay of the new ledger, when accepted
    field_issues: Mapping[UUID, tuple[FieldIssue, ...]] = field(default_factory=dict)
    conflict: Conflict | None = None


def check_ledger(transactions: Iterable[Transaction], assets: Mapping[UUID, Asset]) -> Outcome:
    """Validate every transaction and replay the whole ledger (e.g. an import batch)."""
    txs = tuple(transactions)
    return _evaluate(txs, txs, txs, assets)


def apply_changes(
    current: Iterable[Transaction],
    assets: Mapping[UUID, Asset],
    *,
    upsert: Iterable[Transaction] = (),
    delete_ids: Iterable[UUID] = (),
) -> Outcome:
    """Replace or add `upsert` and remove `delete_ids` as one atomic change."""
    original = tuple(current)
    upserted = tuple(upsert)
    changes = {tx.id: tx for tx in upserted}
    removed = set(delete_ids)
    missing = removed - {tx.id for tx in original}
    if missing:
        raise KeyError(f"unknown transactions: {sorted(map(str, missing))}")
    both = removed & changes.keys()
    if both:
        raise ValueError(f"transactions both edited and deleted: {sorted(map(str, both))}")

    result = [changes.pop(tx.id, tx) for tx in original if tx.id not in removed]
    result.extend(changes.values())  # the remaining ones are new
    return _evaluate(original, tuple(result), upserted, assets)


def create(
    current: Iterable[Transaction], assets: Mapping[UUID, Asset], tx: Transaction
) -> Outcome:
    txs = tuple(current)
    if any(existing.id == tx.id for existing in txs):
        raise ValueError(f"transaction {tx.id} already exists")
    return apply_changes(txs, assets, upsert=[tx])


def edit(current: Iterable[Transaction], assets: Mapping[UUID, Asset], tx: Transaction) -> Outcome:
    return bulk_edit(current, assets, [tx])


def bulk_edit(
    current: Iterable[Transaction], assets: Mapping[UUID, Asset], edited: Iterable[Transaction]
) -> Outcome:
    txs = tuple(current)
    edited = tuple(edited)
    missing = {tx.id for tx in edited} - {tx.id for tx in txs}
    if missing:
        raise KeyError(f"unknown transactions: {sorted(map(str, missing))}")
    return apply_changes(txs, assets, upsert=edited)


def delete(
    current: Iterable[Transaction], assets: Mapping[UUID, Asset], ids: Iterable[UUID]
) -> Outcome:
    return apply_changes(current, assets, delete_ids=ids)


def delete_all_for_asset(
    current: Iterable[Transaction], assets: Mapping[UUID, Asset], asset_id: UUID
) -> Outcome:
    txs = tuple(current)
    return apply_changes(txs, assets, delete_ids=[tx.id for tx in txs if tx.asset_id == asset_id])


def _evaluate(
    original: tuple[Transaction, ...],
    result: tuple[Transaction, ...],
    to_validate: Iterable[Transaction],
    assets: Mapping[UUID, Asset],
) -> Outcome:
    sequences = [tx.sequence for tx in result]
    if len(set(sequences)) != len(sequences):
        raise ValueError("insertion sequences must be unique within an account (D-70)")
    issues = {}
    for tx in to_validate:
        asset = assets.get(tx.asset_id) if tx.asset_id is not None else None
        found = validate_transaction(tx, asset)
        if found:
            issues[tx.id] = tuple(found)
    if issues:
        return Outcome(False, original, field_issues=issues)
    try:
        ledger = replay(result)
    except QuantityConflictError as exc:
        conflict = Conflict(exc.transaction_id, exc.asset_id, exc.quantity, exc.code)
        return Outcome(False, original, conflict=conflict)
    return Outcome(True, result, ledger)
