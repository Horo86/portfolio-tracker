"""Recovery codes of two-factor authentication (D-174). Pure.

Ten single-use codes of 16 characters from the readable alphabet of the temporary passwords
(about 92 bits each), shown once in four groups and stored only as SHA-256 hashes. A code is
random, not chosen by a person, so a fast hash is enough: guessing 92 bits is out of reach
however fast the hash, and a slow one would take the Argon2id slots of D-162.
"""

import hashlib
import hmac
import secrets
from collections.abc import Sequence

from portfolio_tracker.auth.passwords import READABLE_ALPHABET

COUNT = 10
LENGTH = 16
GROUP = 4
_ALPHABET = frozenset(READABLE_ALPHABET)


def new_codes() -> list[str]:
    """Ten new codes as shown to the user, like `tHq4-Rw8e-LmZ2-9Vcx`."""
    return [_grouped("".join(secrets.choice(READABLE_ALPHABET) for _ in range(LENGTH)))
            for _ in range(COUNT)]  # fmt: skip


def _grouped(code: str) -> str:
    return "-".join(code[i : i + GROUP] for i in range(0, LENGTH, GROUP))


def normalize(text: str) -> str | None:
    """The 16 characters of what was typed (spaces and hyphens ignored), or None when it has
    not a recovery code's shape. Codes are case-sensitive."""
    code = "".join(ch for ch in text if not ch.isspace() and ch != "-")
    return code if len(code) == LENGTH and set(code) <= _ALPHABET else None


def hash_code(code: str) -> str:
    """The stored form of a code (given with or without its hyphens)."""
    return hashlib.sha256(code.replace("-", "").encode("ascii")).hexdigest()


def find(typed: str, hashes: Sequence[str]) -> int | None:
    """The index in `hashes` of the code typed, or None. Every hash is compared, in constant
    time and without stopping at a match, so the time taken says nothing about which code,
    if any, was right."""
    code = normalize(typed)
    candidate = hash_code(code or "-" * LENGTH)
    found: int | None = None
    for index, stored in enumerate(hashes):
        if hmac.compare_digest(stored, candidate):
            found = index
    return found if code is not None else None
