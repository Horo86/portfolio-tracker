"""Import batches of one owner (domain-model.md "Import Batch"; D-38, D-109)."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import ImportBatch, Transaction


def add_batch(session: Session, owner_id: UUID, account_id: UUID, **fields: object) -> ImportBatch:
    """The account must already be owner-checked by the caller."""
    batch = ImportBatch(owner_id=owner_id, account_id=account_id, **fields)
    session.add(batch)
    session.flush()
    return batch


def list_batches(
    session: Session, owner_id: UUID, account_id: UUID | None = None
) -> list[ImportBatch]:
    """Newest first."""
    query = select(ImportBatch).where(ImportBatch.owner_id == owner_id)
    if account_id is not None:
        query = query.where(ImportBatch.account_id == account_id)
    return list(session.scalars(query.order_by(ImportBatch.created_at.desc(), ImportBatch.id)))


def latest_batch(session: Session, owner_id: UUID) -> ImportBatch | None:
    query = select(ImportBatch).where(ImportBatch.owner_id == owner_id)
    return session.scalars(
        query.order_by(ImportBatch.created_at.desc(), ImportBatch.id).limit(1)
    ).first()


def get_batch(session: Session, owner_id: UUID, batch_id: UUID) -> ImportBatch | None:
    query = select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.owner_id == owner_id)
    return session.scalars(query).one_or_none()


def count_batch_transactions(session: Session, batch: ImportBatch) -> int:
    """Transactions still linked to the batch (edits keep the link; deletes remove them)."""
    query = select(func.count()).where(Transaction.import_batch_id == batch.id)
    return session.scalar(query) or 0
