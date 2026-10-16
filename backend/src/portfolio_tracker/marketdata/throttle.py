"""One throttle per provider: requests never burst (D-118, D-121, D-128).

- Calls are spaced by at least `min_interval` seconds, across every thread (the queue worker
  and asset searches share one throttle per provider).
- A 429 or a server error pauses the next call for its `Retry-After`, or for a doubling
  back-off when there is none; pauses are capped at `max_pause`, so a broken or hostile
  header cannot stop the app for days.
- After `stop_after_failures` consecutive failures the throttle is *exhausted*: further calls
  are refused without contacting the provider until `reset()` (the next run). A success
  resets the count.
- A caller that cannot wait (a search request) passes `max_wait` and is refused with the wait
  instead of blocking.

The clock and the sleep are injected: tests advance time without sleeping.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from portfolio_tracker.marketdata.base import ErrorCode, ProviderError


@dataclass(frozen=True, slots=True)
class ThrottlePolicy:
    min_interval: float = 1.0  # seconds between two calls
    max_pause: float = 900.0  # cap on any pause (Retry-After or back-off)
    stop_after_failures: int = 5


def _pauses(error: ProviderError) -> bool:
    """Errors that mean "slow down": rate limiting, server trouble, network failures."""
    if error.code in (ErrorCode.RATE_LIMITED, ErrorCode.NETWORK):
        return True
    return error.code is ErrorCode.HTTP_ERROR and (error.status or 0) >= 500


class Throttle:
    def __init__(
        self,
        policy: ThrottlePolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.policy = policy or ThrottlePolicy()
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_free = float("-inf")
        self._backoff = self.policy.min_interval
        self._failures = 0

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    @property
    def exhausted(self) -> bool:
        return self._failures >= self.policy.stop_after_failures

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._backoff = self.policy.min_interval

    def call[T](self, fn: Callable[[], T], *, max_wait: float | None = None) -> T:
        self._acquire(max_wait)
        try:
            result = fn()
        except ProviderError as error:
            self._failed(error)
            raise
        self._succeeded()
        return result

    def _acquire(self, max_wait: float | None) -> None:
        with self._lock:
            if self.exhausted:
                raise ProviderError(ErrorCode.RATE_LIMITED, "provider paused after failures")
            now = self._clock()
            slot = max(now, self._next_free)
            wait = slot - now
            if max_wait is not None and wait > max_wait:
                raise ProviderError(ErrorCode.RATE_LIMITED, "provider busy", retry_after=wait)
            self._next_free = slot + self.policy.min_interval
        if wait > 0:
            self._sleep(wait)

    def _succeeded(self) -> None:
        with self._lock:
            self._failures = 0
            self._backoff = self.policy.min_interval

    def _failed(self, error: ProviderError) -> None:
        with self._lock:
            self._failures += 1
            if not _pauses(error):
                return
            self._backoff = min(self._backoff * 2, self.policy.max_pause)
            pause = error.retry_after if error.retry_after is not None else self._backoff
            pause = min(max(pause, self.policy.min_interval), self.policy.max_pause)
            self._next_free = max(self._next_free, self._clock() + pause)
