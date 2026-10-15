from datetime import UTC, datetime, timedelta

from portfolio_tracker.auth.throttle import LoginThrottle, ThrottlePolicy

T0 = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def fail(throttle: LoginThrottle, keys: list[str], times: int, at: datetime) -> None:
    for _ in range(times):
        throttle.failure(keys, at)


def test_five_failures_block_for_one_minute() -> None:
    throttle = LoginThrottle()
    keys = throttle.keys("Alice", "10.0.0.1")

    fail(throttle, keys, 4, T0)
    assert throttle.retry_after(keys, T0) is None
    fail(throttle, keys, 1, T0)
    assert throttle.retry_after(keys, T0) == 60
    assert throttle.retry_after(keys, T0 + timedelta(seconds=59)) == 1
    assert throttle.retry_after(keys, T0 + timedelta(seconds=60)) is None


def test_block_doubles_and_is_capped() -> None:
    throttle = LoginThrottle()
    keys = throttle.keys("alice", None)
    at = T0
    fail(throttle, keys, 5, at)
    expected = [120, 240, 480, 900, 900]  # 2, 4, 8 min, then the 15-minute cap
    for seconds in expected:
        at += timedelta(seconds=throttle.retry_after(keys, at))
        throttle.failure(keys, at)
        assert throttle.retry_after(keys, at) == seconds


def test_username_key_ignores_case_and_success_clears_it() -> None:
    throttle = LoginThrottle()
    fail(throttle, throttle.keys("Alice", None), 5, T0)

    assert throttle.retry_after(throttle.keys("aLICE", None), T0) == 60
    throttle.success("ALICE")
    assert throttle.retry_after(throttle.keys("alice", None), T0) is None


def test_ip_key_blocks_other_usernames_and_survives_a_success() -> None:
    throttle = LoginThrottle()
    for name in ["a1", "a2", "a3", "a4", "a5"]:  # spraying usernames from one address
        throttle.failure(throttle.keys(name, "10.0.0.9"), T0)

    throttle.success("mallory")  # a valid login of the attacker's own account
    assert throttle.retry_after(throttle.keys("someone", "10.0.0.9"), T0) == 60
    assert throttle.retry_after(throttle.keys("someone", "10.0.0.10"), T0) is None


def test_failures_are_forgotten_after_a_quiet_period() -> None:
    throttle = LoginThrottle()
    keys = throttle.keys("alice", None)
    fail(throttle, keys, 4, T0)

    later = T0 + timedelta(minutes=30)
    throttle.failure(keys, later)
    assert throttle.retry_after(keys, later) is None  # count restarted at 1


def test_memory_is_bounded() -> None:
    throttle = LoginThrottle(ThrottlePolicy(max_keys=10))
    for i in range(50):
        throttle.failure(throttle.keys(f"user{i}", None), T0)

    assert len(throttle._states) == 10
