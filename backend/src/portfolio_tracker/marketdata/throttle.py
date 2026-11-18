"""One throttle per provider: requests never burst (D-118, D-121, D-128).

- Calls are spaced by at least `min_interval` seconds, across every thread (the queue worker
  and asset searches share one throttle per provider).
- A 429 or a server error pauses the next call for its `Retry-After`, or for a doubling
  back-off when there is none; pauses are capped at `max_pause`, so a broken or hostile
  header cannot stop the app for days.
- After `stop_after_failures` consecutive failures the throttle is *exhausted*: further calls
  are refused without contacting the provider until `reset()` (the next run). A success
  resets the count. NOT_FOUND is an answer, not a failure: it neither counts nor resets, so
  a few delisted symbols or stale search results cannot pause the provider for everyone.
  INVENTED_ISIN (a request the transport refused, D-166) never reached the provider: the same.
- A caller that cannot wait (a search request) passes `max_wait` and is refused with the wait
  instead of blocking.

The clock and the sleep are injected: tests advance time without sleeping.

Health (D-186): since the start of the process, the throttle keeps the engine's last success,
its last error (code and time, never the message) and until when it is paused, for the
administrator's price-sources routes. Nothing is stored: a restart starts afresh. NOT_FOUND
counts as a success (the engine answered); INVENTED_ISIN, a refusal before the call and
shutdown record nothing (no request reached the provider). The values form one immutable
`ThrottleHealth`, replaced whole under the throttle's lock, so a reader on another thread
always gets a consistent snapshot, never a mix of two updates.

Shutdown: with a `stopping` event, a call is refused once it is set, whether it would wait
or not (`Stopped`), and the sleep given by the app (`scheduler.stop_aware_sleep`) ends a
wait at once; so no new provider call starts after shutdown begins (ADR-0006).
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from portfolio_tracker.marketdata.base import ErrorCode, ProviderError


class Stopped(BaseException):
    """The app is shutting down. A `BaseException`, so the `except Exception` that contains
    one engine's failure lets it through to the job, which records the run as interrupted."""


@dataclass(frozen=True, slots=True)
class ThrottlePolicy:
    min_interval: float = 1.0  # seconds between two calls
    max_pause: float = 900.0  # cap on any pause (Retry-After or back-off)
    stop_after_failures: int = 5


@dataclass(frozen=True, slots=True)
class ThrottleHealth:
    """One consistent snapshot of an engine's health since the start of the process."""

    last_success_at: datetime | None = None
    last_error_code: ErrorCode | None = None
    last_error_at: datetime | None = None
    paused_until: datetime | None = None  # the end of the last pause (may be past)
    exhausted: bool = False  # refused until the next run after consecutive failures


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
        stopping: threading.Event | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.policy = policy or ThrottlePolicy()
        self._clock = clock
        self._now = now  # wall clock for the health times; `clock` spaces the calls
        self._sleep = sleep
        self._stopping = stopping
        self._lock = threading.Lock()
        self._next_free = float("-inf")
        self._backoff = self.policy.min_interval
        self._failures = 0
        self._health = ThrottleHealth()

    def health(self) -> ThrottleHealth:
        """The current snapshot (read only); replaced whole under the lock by every update."""
        return self._health

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
            self._health = replace(self._health, exhausted=False)

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
        if self._stopping is not None and self._stopping.is_set():
            raise Stopped
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
            self._health = replace(self._health, last_success_at=self._now(), exhausted=False)

    def _failed(self, error: ProviderError) -> None:
        if error.code is ErrorCode.INVENTED_ISIN:
            return  # a request that never left: no engine failure, nothing to report
        if error.code is ErrorCode.NOT_FOUND:
            with self._lock:  # an answer: the engine works, the failure count is untouched
                self._health = replace(self._health, last_success_at=self._now())
            return
        with self._lock:
            self._failures += 1
            at = self._now()
            paused_until = self._health.paused_until
            if _pauses(error):
                self._backoff = min(self._backoff * 2, self.policy.max_pause)
                pause = error.retry_after if error.retry_after is not None else self._backoff
                pause = min(max(pause, self.policy.min_interval), self.policy.max_pause)
                self._next_free = max(self._next_free, self._clock() + pause)
                paused_until = at + timedelta(seconds=pause)
            self._health = replace(
                self._health,
                last_error_code=error.code,
                last_error_at=at,
                paused_until=paused_until,
                exhausted=self.exhausted,
            )
