"""Argon2id password hashing (ADR-0005) and the password policy (min 12 characters, D-21)."""

import secrets

from argon2 import PasswordHasher, profiles
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 1024  # bounds the hashing work an attacker can request

# Readable temporary passwords (D-78): no 0/O, 1/l/I.
_TEMP_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class Passwords:
    """Hashing service. The default profile is RFC 9106 low-memory (64 MiB, t=3, p=4);
    tests pass cheaper parameters through `create_app`, never through configuration."""

    def __init__(self, hasher: PasswordHasher | None = None) -> None:
        self._hasher = hasher or PasswordHasher.from_parameters(profiles.RFC_9106_LOW_MEMORY)
        # Verified when the user does not exist, so both paths cost the same.
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(16))

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def verify_dummy(self, password: str) -> None:
        """Spend the time of a real verification (unknown or disabled user)."""
        self.verify(self._dummy_hash, password)

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)


def policy_error(password: str) -> str | None:
    """None when the password is acceptable, else a stable error code. No composition rules."""
    if len(password) < MIN_LENGTH:
        return "PASSWORD_TOO_SHORT"
    if len(password) > MAX_LENGTH:
        return "PASSWORD_TOO_LONG"
    return None


def temporary_password() -> str:
    """Like `tHq4-Rw8e-LmZ2-9Vcx` (mockup 15): 16 random characters in 4 groups, ~92 bits."""
    chars = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(16))
    return "-".join(chars[i : i + 4] for i in range(0, 16, 4))
