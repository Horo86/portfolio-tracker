"""Stored provider credentials (D-124): one encrypted value per engine.

Instance-wide, no owner. Written by the admin API (M6) and read by the market data service
when it builds a keyed engine. No route ever returns a value; the admin API shows only
whether one is set and when (`credential_updated_at`).
"""

import logging
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from portfolio_tracker.credentials import CredentialCipher, CredentialUnreadable
from portfolio_tracker.db.models import ProviderCredential

log = logging.getLogger(__name__)


def set_credential(session: Session, cipher: CredentialCipher, provider: str, value: str) -> None:
    """Store `value` for `provider`, replacing any earlier one (with a new nonce)."""
    nonce, ciphertext = cipher.encrypt(provider, value)
    row = session.get(ProviderCredential, provider)
    if row is None:
        session.add(ProviderCredential(provider=provider, nonce=nonce, ciphertext=ciphertext))
    else:
        row.nonce, row.ciphertext = nonce, ciphertext
    session.flush()
    log.info("provider credential stored", extra={"provider": provider})


def get_credential(session: Session, cipher: CredentialCipher, provider: str) -> str | None:
    """The decrypted value; None when none is stored. Raises `CredentialUnreadable`."""
    row = session.get(ProviderCredential, provider)
    if row is None:
        return None
    return cipher.decrypt(provider, row.nonce, row.ciphertext)


def delete_credential(session: Session, provider: str) -> bool:
    """Remove the stored value; False when there was none."""
    result = session.execute(
        delete(ProviderCredential).where(ProviderCredential.provider == provider)
    )
    deleted = bool(result.rowcount)
    if deleted:
        log.info("provider credential deleted", extra={"provider": provider})
    return deleted


def credential_updated_at(session: Session, provider: str) -> datetime | None:
    """When the value was last set; None when none is stored. Nothing is decrypted."""
    row = session.get(ProviderCredential, provider)
    return row.updated_at if row is not None else None


def credential_lookup(session: Session, cipher: CredentialCipher) -> Callable[[str], str | None]:
    """`credential_for` for `Registry.build`: a value that does not decrypt counts as missing
    (the engine is skipped), with a warning naming the provider only."""

    def lookup(provider: str) -> str | None:
        try:
            return get_credential(session, cipher, provider)
        except CredentialUnreadable:
            log.warning(
                "provider credential cannot be read: another key or altered data",
                extra={"provider": provider},
            )
            return None

    return lookup
