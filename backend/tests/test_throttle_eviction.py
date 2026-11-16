"""What the login throttle drops when its table is full (`LoginThrottle._evict`, D-82)."""

from datetime import timedelta
from uuid import uuid4

import pytest
from test_throttle import T0, fail, retry_after

from portfolio_tracker.auth.throttle import LoginThrottle, ThrottlePolicy, login_keys, totp_keys


def table(throttle: LoginThrottle) -> list[str]:
    return [key.partition(":")[2] for key in throttle._states]


def test_a_table_full_of_blocked_keys_cannot_push_out_a_fresh_count() -> None:
    """Blocked keys are spared only in the oldest quarter. Spared everywhere (as before
    1.0.1), an attacker who keeps the table full of blocked keys of their own would push out
    the victim's count with a couple of new keys, and guess without ever being blocked."""
    throttle = LoginThrottle(ThrottlePolicy(max_keys=10))
    for i in range(9):  # the attacker's own keys, all blocked
        fail(throttle, login_keys(f"pin{i}", None), 5)
    victim = login_keys("victim", None)
    fail(throttle, victim, 4)

    for i in range(3):  # new keys: the table is over its cap each time
        fail(throttle, login_keys(f"new{i}", None), 1)

    assert len(throttle._states) == 10
    assert "victim" in table(throttle)
    assert table(throttle)[:2] == ["pin3", "pin4"]  # the oldest blocked keys went instead
    fail(throttle, victim, 1)
    assert retry_after(throttle, victim) == 60  # the fifth failure blocks: the four counted


@pytest.mark.parametrize("failures", [1, 4, 5])  # other keys below the threshold, or blocked
def test_no_count_is_dropped_before_three_quarters_of_the_cap_were_used_after_it(
    failures: int,
) -> None:
    cap = 20
    throttle = LoginThrottle(ThrottlePolicy(max_keys=cap))
    for i in range(cap):  # a full table, whatever is in it
        fail(throttle, login_keys(f"old{i}", None), failures)
    victim = login_keys("victim", None)
    fail(throttle, victim, 4)

    for i in range(cap * 3 // 4 - 1):
        fail(throttle, login_keys(f"new{i}", None), failures)
        assert len(throttle._states) <= cap
    assert "victim" in table(throttle)

    for i in range(cap):  # with enough other keys any count goes: the table is bounded
        fail(throttle, login_keys(f"more{i}", None), failures)
    assert "victim" not in table(throttle) and len(throttle._states) == cap


def test_counts_that_would_be_forgotten_anyway_are_dropped_first() -> None:
    throttle = LoginThrottle(ThrottlePolicy(max_keys=10))
    codes = totp_keys(uuid4())
    fail(throttle, codes, 4)  # remembered for a day, the oldest key of the table
    for i in range(9):
        fail(throttle, login_keys(f"user{i}", None), 4)  # remembered for 30 minutes
    later = T0 + timedelta(minutes=31)

    for i in range(5):
        fail(throttle, login_keys(f"new{i}", None), 1, later)

    assert codes[0] in throttle._states  # older than all of them, but still worth keeping
    assert table(throttle)[1:] == [f"user{i}" for i in range(5, 9)] + [f"new{i}" for i in range(5)]
    fail(throttle, codes, 1, later)
    assert retry_after(throttle, codes, later) == 60


def test_memory_stays_bounded_when_every_key_is_blocked_or_in_flight() -> None:
    """The cap holds after every operation, also with nothing idle to drop; an attempt whose
    key was dropped while it ran still finishes and counts."""
    throttle = LoginThrottle(ThrottlePolicy(max_keys=4))
    for i in range(4):
        fail(throttle, login_keys(f"blocked{i}", None), 5)
    assert len(throttle._states) == 4

    running = [throttle.attempt(login_keys(f"slow{i}", None), T0) for i in range(8)]
    for attempt in running:
        assert not attempt.__enter__().refused
        assert len(throttle._states) <= 4
    for attempt in running:
        attempt.__exit__(None, None, None)
        assert len(throttle._states) <= 4

    assert all(state.in_flight == 0 for state in throttle._states.values())
    for i in range(1000):  # and a long flood of new usernames and addresses
        fail(throttle, login_keys(f"user{i}", f"192.0.2.{i % 250}"), 1)
        assert len(throttle._states) <= 4
