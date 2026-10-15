"""Minimal login brute-force protection (M2; configurable thresholds and trusted proxies: M6).

Failed logins are counted per username and per client IP. After `free_failures` consecutive
failures a key is blocked for `first_block`; every further failure while the count is high
doubles the block, up to `max_block`. A success clears the username key (not the IP key, so
an attacker cannot reset it by logging into their own account). Counts are forgotten after
`forget_after` without failures. State is in memory: a restart clears it, which is acceptable
for a single-process household instance (ADR-0006: one worker).
"""

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class ThrottlePolicy:
    free_failures: int = 5
    first_block: timedelta = timedelta(minutes=1)
    max_block: timedelta = timedelta(minutes=15)
    forget_after: timedelta = timedelta(minutes=30)
    max_keys: int = 10_000  # bounds memory when many usernames or IPs are tried


@dataclass(slots=True)
class _State:
    failures: int
    last_failure: datetime
    blocked_until: datetime | None = None


class LoginThrottle:
    def __init__(self, policy: ThrottlePolicy | None = None) -> None:
        self.policy = policy or ThrottlePolicy()
        self._states: OrderedDict[str, _State] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def keys(username: str, ip: str | None) -> list[str]:
        keys = [f"user:{username.casefold()}"]
        if ip:
            keys.append(f"ip:{ip}")
        return keys

    def retry_after(self, keys: list[str], now: datetime) -> int | None:
        """Seconds until a login may be tried again, or None when not blocked."""
        with self._lock:
            waits = []
            for key in keys:
                state = self._current(key, now)
                if state and state.blocked_until and state.blocked_until > now:
                    waits.append((state.blocked_until - now).total_seconds())
            return math.ceil(max(waits)) if waits else None

    def failure(self, keys: list[str], now: datetime) -> None:
        with self._lock:
            for key in keys:
                state = self._current(key, now)
                if state is None:
                    state = _State(failures=0, last_failure=now)
                state.failures += 1
                state.last_failure = now
                over = state.failures - self.policy.free_failures
                if over >= 0:
                    block = min(self.policy.first_block * 2**over, self.policy.max_block)
                    state.blocked_until = now + block
                self._states[key] = state
                self._states.move_to_end(key)
            while len(self._states) > self.policy.max_keys:
                self._states.popitem(last=False)

    def success(self, username: str) -> None:
        with self._lock:
            self._states.pop(self.keys(username, None)[0], None)

    def _current(self, key: str, now: datetime) -> _State | None:
        state = self._states.get(key)
        if state is None:
            return None
        blocked = state.blocked_until is not None and state.blocked_until > now
        if not blocked and now - state.last_failure >= self.policy.forget_after:
            del self._states[key]
            return None
        return state
