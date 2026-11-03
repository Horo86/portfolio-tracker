"""Argon2id password hashing (ADR-0005) and the password policy (min 12 characters, D-21)."""

import math
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from argon2 import PasswordHasher, profiles
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 1024  # bounds the hashing work an attacker can request

# Readable temporary passwords (D-78): no 0/O, 1/l/I.
_TEMP_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# R-14 (D-162): every Argon2id operation takes 64 MiB, so at most SLOTS run at once in the
# process. Routes are plain functions run in the worker thread pool (40 threads), so waiting
# never blocks the event loop; at most MAX_WAITING requests wait, so a burst cannot take the
# threads every other route needs.
SLOTS = 2
WAIT_SECONDS = 5.0
MAX_WAITING = 8


class PasswordChecksBusy(Exception):
    """No slot freed within the wait: answered 429 TOO_MANY_ATTEMPTS, counted by no throttle."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("the server is busy checking passwords")
        self.retry_after = retry_after


class Passwords:
    """Hashing service. The default profile is RFC 9106 low-memory (64 MiB, t=3, p=4);
    tests pass cheaper parameters and a shorter `wait` through `create_app`, never through
    configuration. Every hash and verification takes one of the shared slots (D-162)."""

    def __init__(self, hasher: PasswordHasher | None = None, *, wait: float = WAIT_SECONDS) -> None:
        self._hasher = hasher or PasswordHasher.from_parameters(profiles.RFC_9106_LOW_MEMORY)
        self._wait = wait
        self._slots = threading.BoundedSemaphore(SLOTS)
        self._lock = threading.Lock()
        self._in_use = 0
        self._waiting = 0
        # Verified when the user does not exist, so both paths cost the same.
        self._dummy_hash = self.hash(secrets.token_urlsafe(16))

    @property
    def in_use(self) -> int:
        return self._in_use

    @property
    def waiting(self) -> int:
        return self._waiting

    @contextmanager
    def _slot(self) -> Iterator[None]:
        busy = PasswordChecksBusy(retry_after=max(1, math.ceil(self._wait)))
        with self._lock:
            if self._waiting >= MAX_WAITING:
                raise busy
            self._waiting += 1
        try:
            acquired = self._slots.acquire(timeout=self._wait)
        finally:
            with self._lock:
                self._waiting -= 1
        if not acquired:
            raise busy
        with self._lock:
            self._in_use += 1
        try:
            yield
        finally:  # every path: a wrong password, an exception, a client that went away
            with self._lock:
                self._in_use -= 1
            self._slots.release()

    def hash(self, password: str) -> str:
        with self._slot():
            return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        with self._slot():
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
