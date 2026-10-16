"""Login/password-change throttle (D-82)."""

import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from portfolio_tracker.auth.throttle import (
    LoginThrottle,
    ThrottlePolicy,
    login_keys,
    password_change_keys,
)

T0 = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def fail(throttle: LoginThrottle, keys: list[str], times: int, at: datetime = T0) -> None:
    for _ in range(times):
        with throttle.attempt(keys, at) as attempt:
            assert not attempt.refused


def succeed(throttle: LoginThrottle, keys: list[str], at: datetime = T0) -> None:
    with throttle.attempt(keys, at) as attempt:
        assert not attempt.refused
        attempt.success()


def retry_after(throttle: LoginThrottle, keys: list[str], at: datetime = T0) -> int | None:
    """Probe without consuming: a refused attempt records nothing; an admitted one is
    released as a success only on keys that a success does not clear."""
    attempt = throttle._reserve(keys, at)
    if attempt.refused:
        return attempt.retry_after
    with throttle._lock:  # undo the reservation
        for key in keys:
            throttle._states[key].in_flight -= 1
    return None


def test_five_username_failures_block_for_one_minute() -> None:
    throttle = LoginThrottle()
    keys = login_keys("Alice", "10.0.0.1")

    fail(throttle, keys, 4)
    assert retry_after(throttle, keys) is None
    fail(throttle, keys, 1)
    assert retry_after(throttle, keys) == 60
    assert retry_after(throttle, keys, T0 + timedelta(seconds=59)) == 1
    assert retry_after(throttle, keys, T0 + timedelta(seconds=60)) is None


def test_block_doubles_and_is_capped() -> None:
    throttle = LoginThrottle()
    keys = login_keys("alice", None)
    at = T0
    fail(throttle, keys, 5, at)
    for seconds in [120, 240, 480, 900, 900]:  # 2, 4, 8 min, then the 15-minute cap
        at += timedelta(seconds=retry_after(throttle, keys, at))
        fail(throttle, keys, 1, at)  # one attempt is admitted after each block
        assert retry_after(throttle, keys, at) == seconds


def test_ip_threshold_is_higher_than_the_username_one() -> None:
    """Behind a reverse proxy the whole household shares one IP (D-82)."""
    throttle = LoginThrottle()
    for i in range(29):  # different usernames, same address
        fail(throttle, login_keys(f"user{i}", "10.0.0.9"), 1)
    assert retry_after(throttle, login_keys("mum", "10.0.0.9")) is None

    fail(throttle, login_keys("user29", "10.0.0.9"), 1)  # 30th failure from the address
    assert retry_after(throttle, login_keys("mum", "10.0.0.9")) == 60
    assert retry_after(throttle, login_keys("mum", "10.0.0.10")) is None


def test_username_key_ignores_case_and_success_clears_it() -> None:
    throttle = LoginThrottle()
    fail(throttle, login_keys("Alice", None), 4)
    succeed(throttle, login_keys("ALICE", None))

    fail(throttle, login_keys("alice", None), 4)
    assert retry_after(throttle, login_keys("aLiCe", None)) is None  # counter restarted


def test_success_never_clears_the_ip_key() -> None:
    throttle = LoginThrottle()
    for i in range(29):
        fail(throttle, login_keys(f"victim{i}", "10.0.0.9"), 1)
    succeed(throttle, login_keys("mallory", "10.0.0.9"))  # the attacker's own account

    fail(throttle, login_keys("victim29", "10.0.0.9"), 1)
    assert retry_after(throttle, login_keys("someone", "10.0.0.9")) == 60


def test_password_change_key_is_per_user() -> None:
    throttle = LoginThrottle()
    alice, bob = uuid4(), uuid4()
    fail(throttle, password_change_keys(alice), 5)

    assert retry_after(throttle, password_change_keys(alice)) == 60
    assert retry_after(throttle, password_change_keys(bob)) is None


def test_failures_are_forgotten_after_a_quiet_period() -> None:
    throttle = LoginThrottle()
    keys = login_keys("alice", None)
    fail(throttle, keys, 4)

    later = T0 + timedelta(minutes=30)
    fail(throttle, keys, 4, later)  # count restarted: 4 more are free
    assert retry_after(throttle, keys, later) is None


def test_an_exception_during_the_check_counts_as_a_failure() -> None:
    throttle = LoginThrottle()
    keys = login_keys("alice", None)
    for _ in range(5):
        try:
            with throttle.attempt(keys, T0):
                raise RuntimeError("database unavailable")
        except RuntimeError:
            pass

    assert retry_after(throttle, keys) == 60


def test_concurrent_attempts_cannot_exceed_the_free_failures() -> None:
    """A burst of parallel guesses: only the free attempts are admitted at once."""
    throttle = LoginThrottle()
    keys = login_keys("alice", None)
    start, release = threading.Barrier(40), threading.Event()
    admitted, refused = [], []

    def guess() -> None:
        start.wait()
        with throttle.attempt(keys, T0) as attempt:
            (refused if attempt.refused else admitted).append(attempt)
            if not attempt.refused:
                release.wait(5)  # still checking the password

    threads = [threading.Thread(target=guess) for _ in range(40)]
    for thread in threads:
        thread.start()
    while len(admitted) + len(refused) < 40:
        pass
    release.set()
    for thread in threads:
        thread.join()

    assert len(admitted) == 5 and len(refused) == 35
    assert retry_after(throttle, keys) == 60  # the 5 failures then block the username


def test_a_refusal_is_logged_once_per_block() -> None:
    throttle = LoginThrottle()
    keys = login_keys("alice", None)
    fail(throttle, keys, 5)

    flags = []
    for _ in range(3):
        with throttle.attempt(keys, T0) as attempt:
            flags.append(attempt.first_refusal)
    assert flags == [True, False, False]


def test_memory_is_bounded_without_evicting_active_blocks() -> None:
    throttle = LoginThrottle(ThrottlePolicy(max_keys=10))
    blocked = login_keys("victim", None)
    fail(throttle, blocked, 5)
    for i in range(50):  # flood of new keys
        fail(throttle, login_keys(f"user{i}", None), 1)

    assert len(throttle._states) == 10
    assert retry_after(throttle, blocked) == 60  # the block survived the flood
