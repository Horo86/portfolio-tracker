"""The intermediate proof of a two-step login (D-174): the password was right, the code is
still to come.

It is not a session: it has no row in `sessions`, authenticates no route and can only be
exchanged, with a valid code, at the second step. The token is 256 random bits; only its
SHA-256 is kept, in memory (one process, ADR-0006), so a restart clears every pending login and
the user starts again from the password. A proof lasts `LIFETIME` and `MAX_ATTEMPTS` code
attempts; a wrong code does not consume it, a success does.
"""

import hashlib
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

LIFETIME = timedelta(minutes=5)
MAX_ATTEMPTS = 5
MAX_PENDING = 10_000  # bounds memory; the oldest pending login goes first


@dataclass(frozen=True, slots=True)
class Pending:
    """What the second step needs to know about the first."""

    user_id: UUID
    password_hash: str  # as verified: a password changed meanwhile ends the pending login
    issue_token: bool  # a bearer client asked for the session token in the body
    expires_at: datetime


@dataclass(slots=True)
class _Entry:
    pending: Pending
    attempts_left: int = MAX_ATTEMPTS


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginChallenges:
    def __init__(self) -> None:
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def issue(self, user_id: UUID, password_hash: str, issue_token: bool, at: datetime) -> str:
        """A new proof for a user whose password was just verified. Returns the token, given
        to the client once."""
        token = secrets.token_urlsafe(32)
        pending = Pending(user_id, password_hash, issue_token, at + LIFETIME)
        with self._lock:
            self._drop_expired(at)
            self._entries[_key(token)] = _Entry(pending)
            while len(self._entries) > MAX_PENDING:
                self._entries.popitem(last=False)
        return token

    def peek(self, token: str, at: datetime) -> Pending | None:
        """The pending login of a live proof, without using an attempt."""
        with self._lock:
            entry = self._live(token, at)
            return entry.pending if entry else None

    def take_attempt(self, token: str, at: datetime) -> tuple[Pending, int] | None:
        """Use one of the proof's attempts, before the code is checked, so parallel requests
        cannot try more codes than the proof allows. Returns the pending login and how many
        attempts are left; the proof is gone once none is."""
        with self._lock:
            entry = self._live(token, at)
            if entry is None:
                return None
            entry.attempts_left -= 1
            if entry.attempts_left <= 0:
                del self._entries[_key(token)]
            return entry.pending, entry.attempts_left

    def consume(self, token: str) -> None:
        """The login is complete, or can no longer be: the proof is gone."""
        with self._lock:
            self._entries.pop(_key(token), None)

    def drop_user(self, user_id: UUID) -> int:
        """Forget every pending login of a user (disabled, password reset). Returns how many."""
        with self._lock:
            keys = [key for key, entry in self._entries.items() if entry.pending.user_id == user_id]
            for key in keys:
                del self._entries[key]
            return len(keys)

    def _live(self, token: str, at: datetime) -> _Entry | None:
        key = _key(token)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if at >= entry.pending.expires_at:
            del self._entries[key]
            return None
        return entry

    def _drop_expired(self, at: datetime) -> None:
        expired = [key for key, entry in self._entries.items() if at >= entry.pending.expires_at]
        for key in expired:
            del self._entries[key]
