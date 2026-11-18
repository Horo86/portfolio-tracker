"""The provider throttle (D-118, D-121, D-128) with a fake clock: no test sleeps."""

import contextlib
import threading
from datetime import UTC, datetime, timedelta

import pytest

from portfolio_tracker.marketdata.base import ErrorCode, ProviderError
from portfolio_tracker.marketdata.throttle import (
    Stopped,
    Throttle,
    ThrottleHealth,
    ThrottlePolicy,
)


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.now += seconds


def make(**policy: float) -> tuple[Throttle, FakeTime]:
    time = FakeTime()
    defaults = {"min_interval": 2.0, "max_pause": 600.0, "stop_after_failures": 3}
    return Throttle(ThrottlePolicy(**{**defaults, **policy}), time.clock, time.sleep), time


def rate_limited(retry_after: float | None = None) -> ProviderError:
    return ProviderError(ErrorCode.RATE_LIMITED, "x", status=429, retry_after=retry_after)


def fail(error: ProviderError):
    def call() -> None:
        raise error

    return call


def test_calls_are_spaced_by_the_minimum_interval() -> None:
    throttle, time = make()
    assert throttle.call(lambda: "a") == "a"
    throttle.call(lambda: "b")
    time.now += 0.5
    throttle.call(lambda: "c")
    assert time.sleeps == [2.0, 1.5]


def test_no_call_starts_once_the_app_is_stopping() -> None:
    """Final review M-4: even a call that would not wait is refused after shutdown began,
    without contacting the provider."""
    stopping, called = threading.Event(), []
    time = FakeTime()
    throttle = Throttle(ThrottlePolicy(min_interval=1.0), time.clock, time.sleep, stopping)
    throttle.call(lambda: called.append("before"))
    time.now += 5  # the interval has passed: no wait
    stopping.set()
    with pytest.raises(Stopped):
        throttle.call(lambda: called.append("after"))
    assert called == ["before"] and time.sleeps == []


def test_retry_after_pauses_the_next_call() -> None:
    throttle, time = make()
    with pytest.raises(ProviderError):
        throttle.call(fail(rate_limited(30)))
    throttle.call(lambda: None)
    assert time.sleeps == [30.0]


def test_without_retry_after_the_pause_doubles_and_resets_on_success() -> None:
    throttle, time = make(stop_after_failures=10)
    for _ in range(3):
        with pytest.raises(ProviderError):
            throttle.call(fail(ProviderError(ErrorCode.HTTP_ERROR, "x", status=503)))
    assert time.sleeps == [4.0, 8.0]  # after the 1st and 2nd failures
    throttle.call(lambda: None)
    assert time.sleeps[-1] == 16.0
    throttle.call(lambda: None)
    assert time.sleeps[-1] == 2.0  # back to the plain interval


def test_pauses_are_capped() -> None:
    throttle, time = make(max_pause=60.0)
    with pytest.raises(ProviderError):
        throttle.call(fail(rate_limited(86_400)))  # a hostile or broken header
    throttle.call(lambda: None)
    assert time.sleeps == [60.0]


def test_not_found_and_format_errors_do_not_pause_but_count() -> None:
    throttle, time = make()
    with pytest.raises(ProviderError):
        throttle.call(fail(ProviderError(ErrorCode.FORMAT_CHANGED, "x")))
    throttle.call(lambda: None)
    assert time.sleeps == [2.0]
    assert throttle.consecutive_failures == 0  # reset by the success


def test_a_run_stops_after_consecutive_failures() -> None:
    throttle, _ = make(stop_after_failures=2)
    for _ in range(2):
        with pytest.raises(ProviderError):
            throttle.call(fail(ProviderError(ErrorCode.NETWORK, "x")))
    assert throttle.exhausted
    with pytest.raises(ProviderError) as error:
        throttle.call(lambda: pytest.fail("must not be called"))
    assert error.value.code is ErrorCode.RATE_LIMITED
    throttle.reset()
    assert not throttle.exhausted
    assert throttle.call(lambda: 1) == 1


def test_a_caller_that_cannot_wait_is_refused_with_the_wait() -> None:
    throttle, time = make()
    with pytest.raises(ProviderError):
        throttle.call(fail(rate_limited(45)))
    with pytest.raises(ProviderError) as error:
        throttle.call(lambda: pytest.fail("must not be called"), max_wait=5)
    assert error.value.code is ErrorCode.RATE_LIMITED
    assert error.value.retry_after == 45
    assert time.sleeps == []


def test_concurrent_callers_get_distinct_slots() -> None:
    lock = threading.Lock()
    slots: list[float] = []
    time = FakeTime()

    def sleep(seconds: float) -> None:  # record the slot each caller was given
        with lock:
            slots.append(time.now + seconds)

    throttle = Throttle(ThrottlePolicy(min_interval=1.0), time.clock, sleep)
    threads = [threading.Thread(target=throttle.call, args=(lambda: None,)) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(slots) == [1001.0, 1002.0, 1003.0, 1004.0]  # the first one did not wait


def test_not_found_neither_counts_nor_pauses() -> None:
    throttle, time = make(stop_after_failures=2)
    with pytest.raises(ProviderError):
        throttle.call(fail(ProviderError(ErrorCode.NETWORK, "x")))
    for _ in range(5):  # e.g. delisted symbols in a daily run
        with pytest.raises(ProviderError):
            throttle.call(fail(ProviderError(ErrorCode.NOT_FOUND, "x", status=404)))
    assert throttle.consecutive_failures == 1 and not throttle.exhausted
    assert time.sleeps == [4.0, 2.0, 2.0, 2.0, 2.0]  # one back-off, then the plain interval


# --- health since the start of the process (D-186) -------------------------------------


class Wall:
    """A wall clock that moves one second at each reading."""

    def __init__(self) -> None:
        self.at = datetime(2026, 3, 5, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.at += timedelta(seconds=1)
        return self.at


def with_wall(**policy: float) -> tuple[Throttle, FakeTime, Wall]:
    time, wall = FakeTime(), Wall()
    defaults = {"min_interval": 2.0, "max_pause": 600.0, "stop_after_failures": 3}
    policy = ThrottlePolicy(**{**defaults, **policy})
    return Throttle(policy, time.clock, time.sleep, now=wall), time, wall


def test_a_new_throttle_has_an_empty_health() -> None:
    throttle, _, _ = with_wall()
    assert throttle.health() == ThrottleHealth()


def test_a_success_and_a_not_found_are_recorded_as_successes() -> None:
    throttle, _, wall = with_wall()
    throttle.call(lambda: None)
    first = throttle.health().last_success_at
    with pytest.raises(ProviderError):
        throttle.call(fail(ProviderError(ErrorCode.NOT_FOUND, "x")))
    health = throttle.health()
    assert first is not None and health.last_success_at == wall.at > first
    assert health.last_error_code is None and health.last_error_at is None


def test_an_invented_isin_records_nothing() -> None:
    throttle, _, _ = with_wall()
    with pytest.raises(ProviderError):
        throttle.call(fail(ProviderError(ErrorCode.INVENTED_ISIN, "x")))
    assert throttle.health() == ThrottleHealth()


def test_a_rate_limit_records_the_code_its_time_and_the_pause() -> None:
    throttle, _, wall = with_wall()
    with pytest.raises(ProviderError):
        throttle.call(fail(rate_limited(30)))
    health = throttle.health()
    assert health.last_error_code is ErrorCode.RATE_LIMITED
    assert health.last_error_at == wall.at
    assert health.paused_until == wall.at + timedelta(seconds=30)
    assert health.last_success_at is None and not health.exhausted


def test_an_error_that_does_not_pause_keeps_the_earlier_pause_end() -> None:
    throttle, _, _ = with_wall(stop_after_failures=10)
    with pytest.raises(ProviderError):
        throttle.call(fail(rate_limited(30)))
    paused = throttle.health().paused_until
    with pytest.raises(ProviderError):
        throttle.call(fail(ProviderError(ErrorCode.FORMAT_CHANGED, "x")))
    health = throttle.health()
    assert health.last_error_code is ErrorCode.FORMAT_CHANGED
    assert health.paused_until == paused


def test_exhaustion_is_in_the_health_until_the_reset() -> None:
    throttle, _, _ = with_wall()
    for _ in range(3):
        with pytest.raises(ProviderError):
            throttle.call(fail(ProviderError(ErrorCode.HTTP_ERROR, "x", status=503)))
    assert throttle.health().exhausted
    with pytest.raises(ProviderError):
        throttle.call(lambda: None)  # refused without calling: nothing new recorded
    assert throttle.health().last_success_at is None
    throttle.reset()
    assert not throttle.health().exhausted


def test_the_health_never_mixes_two_updates() -> None:
    """The job thread writes while request threads read: each read is one snapshot, so a
    pause end always belongs to the error time it was recorded with."""
    throttle, _, _ = with_wall(min_interval=0.0, stop_after_failures=10**9)
    pause = timedelta(seconds=30)
    done = threading.Event()
    seen: list[ThrottleHealth] = []

    def write() -> None:
        for turn in range(3000):
            call = fail(rate_limited(30)) if turn % 2 else (lambda: None)
            with contextlib.suppress(ProviderError):
                throttle.call(call)
        done.set()

    writer = threading.Thread(target=write)
    writer.start()
    while not done.is_set():
        seen.append(throttle.health())
    writer.join()
    seen.append(throttle.health())
    errors = [h for h in seen if h.last_error_at is not None]
    assert errors
    for health in errors:
        assert health.paused_until == health.last_error_at + pause
        assert health.last_error_code is ErrorCode.RATE_LIMITED
