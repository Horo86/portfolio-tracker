"""Minimal brute-force protection for password checks (D-82; configurable thresholds and
trusted proxies: M6).

Failed attempts are counted per key. Login uses a username key and a client-IP key; the
password change uses a per-user key. A key of kind K allows `free_failures[K]` consecutive
failures; then it is blocked for `first_block`, and every further failure doubles the block
up to `max_block`. The IP threshold is much higher than the username one because behind a
reverse proxy every household member shares one address (D-82).

An attempt is *reserved* before the password is checked and counted as a failure unless it
succeeds, so parallel requests cannot all pass the check before the first failure is
recorded: at most the remaining free failures can be in flight at once.

A success clears the username and password-change keys, never the IP key (an attacker must
not reset it by logging into their own account). Counts are forgotten after `forget_after`
without failures. State is in memory: a restart clears it, acceptable for a single process
(ADR-0006: one worker).
"""

import math
import threading
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from uuid import UUID

USER, IP, PASSWORD_CHANGE = "user", "ip", "pwchange"


@dataclass(frozen=True, slots=True)
class ThrottlePolicy:
    free_failures: Mapping[str, int] = field(
        default_factory=lambda: MappingProxyType({USER: 5, IP: 30, PASSWORD_CHANGE: 5})
    )
    first_block: timedelta = timedelta(minutes=1)
    max_block: timedelta = timedelta(minutes=15)
    forget_after: timedelta = timedelta(minutes=30)
    max_keys: int = 10_000  # bounds memory when many usernames or IPs are tried
    cleared_on_success: frozenset[str] = frozenset({USER, PASSWORD_CHANGE})


@dataclass(slots=True)
class _State:
    failures: int = 0
    in_flight: int = 0
    last_failure: datetime | None = None
    blocked_until: datetime | None = None
    refusal_logged: bool = False


@dataclass(slots=True)
class Attempt:
    """One reserved password check. `retry_after` is set when the attempt was refused."""

    retry_after: int | None = None
    first_refusal: bool = False  # log a refusal once per block, not once per request
    succeeded: bool = False

    @property
    def refused(self) -> bool:
        return self.retry_after is not None

    def success(self) -> None:
        self.succeeded = True


def login_keys(username: str, ip: str | None) -> list[str]:
    keys = [f"{USER}:{username.casefold()}"]
    if ip:
        keys.append(f"{IP}:{ip}")
    return keys


def password_change_keys(user_id: UUID) -> list[str]:
    return [f"{PASSWORD_CHANGE}:{user_id}"]


class LoginThrottle:
    def __init__(self, policy: ThrottlePolicy | None = None) -> None:
        self.policy = policy or ThrottlePolicy()
        self._states: OrderedDict[str, _State] = OrderedDict()
        self._lock = threading.Lock()

    @contextmanager
    def attempt(self, keys: list[str], now: datetime) -> Iterator[Attempt]:
        """Reserve an attempt; on exit it counts as a failure unless `success()` was called
        (also when the check raised)."""
        attempt = self._reserve(keys, now)
        try:
            yield attempt
        finally:
            if not attempt.refused:
                self._finish(keys, now, attempt.succeeded)

    def _reserve(self, keys: list[str], now: datetime) -> Attempt:
        with self._lock:
            states = [(key, self._current(key, now)) for key in keys]
            waits, first = [], False
            for key, state in states:
                if state is None:
                    continue
                if state.blocked_until and state.blocked_until > now:
                    waits.append((state.blocked_until - now).total_seconds())
                    if not state.refusal_logged:
                        state.refusal_logged = first = True
                elif state.in_flight >= max(1, self._free(key) - state.failures):
                    # Only the remaining free attempts may run at once (one after a block).
                    waits.append(1)
            if waits:
                return Attempt(retry_after=max(1, math.ceil(max(waits))), first_refusal=first)
            for key, state in states:
                if state is None:
                    state = self._states[key] = _State()
                state.in_flight += 1
                self._states.move_to_end(key)
            self._evict(now)
            return Attempt()

    def _finish(self, keys: list[str], now: datetime, succeeded: bool) -> None:
        with self._lock:
            for key in keys:
                state = self._states.get(key)
                if state is None:  # evicted meanwhile
                    state = self._states[key] = _State(in_flight=1)
                state.in_flight -= 1
                if succeeded:
                    if self._kind(key) in self.policy.cleared_on_success:
                        del self._states[key]
                    continue
                state.failures += 1
                state.last_failure = now
                over = state.failures - self._free(key)
                if over >= 0:
                    block = min(self.policy.first_block * 2**over, self.policy.max_block)
                    state.blocked_until = now + block
                    state.refusal_logged = False
            self._evict(now)

    def _current(self, key: str, now: datetime) -> _State | None:
        state = self._states.get(key)
        if state is None or state.in_flight:
            return state
        blocked = state.blocked_until is not None and state.blocked_until > now
        quiet = state.last_failure is None or now - state.last_failure >= self.policy.forget_after
        if not blocked and quiet:
            del self._states[key]
            return None
        return state

    def _evict(self, now: datetime) -> None:
        """Over the cap, drop the oldest keys that are neither blocked nor in flight first,
        so flooding new keys cannot reset an active block."""
        excess = len(self._states) - self.policy.max_keys
        if excess <= 0:
            return
        idle = [
            key
            for key, state in self._states.items()
            if not state.in_flight and not (state.blocked_until and state.blocked_until > now)
        ]
        for key in idle[:excess]:
            del self._states[key]
        while len(self._states) > self.policy.max_keys:  # only blocked keys left
            self._states.popitem(last=False)

    def _free(self, key: str) -> int:
        return self.policy.free_failures[self._kind(key)]

    @staticmethod
    def _kind(key: str) -> str:
        return key.partition(":")[0]
