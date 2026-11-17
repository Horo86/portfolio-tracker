"""The security settings module (D-177): bounds, cross rules, the environment's values, the
policies they become, and the runtime plumbing. Invented data only."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portfolio_tracker.auth import security_settings as ss
from portfolio_tracker.auth.sessions import SessionPolicy
from portfolio_tracker.auth.throttle import (
    IP,
    PASSWORD_CHANGE,
    TOTP,
    USER,
    LoginThrottle,
    ThrottlePolicy,
    login_keys,
    totp_keys,
)
from portfolio_tracker.config import Settings
from portfolio_tracker.main import create_app
from portfolio_tracker.services import market_startup

T0 = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
VALID = {
    "session_idle_days": 7,
    "session_absolute_days": 30,
    "login_account_failures": 5,
    "login_address_failures": 30,
    "block_first_minutes": 1,
    "block_max_minutes": 15,
}


def with_(**changes: object) -> dict[str, object]:
    return {**VALID, **changes}


# --- bounds and cross rules ------------------------------------------------------------------


def test_the_defaults_are_todays_values_and_valid() -> None:
    assert ss.problems(VALID) == []
    assert ss.SecuritySettings(**VALID) == ss.DEFAULTS
    policy = ThrottlePolicy()
    assert (policy.free_failures[USER], policy.free_failures[IP]) == (5, 30)
    assert (policy.first_block, policy.max_block, policy.forget_after) == (
        timedelta(minutes=1),
        timedelta(minutes=15),
        timedelta(minutes=30),
    )


EDGES = [
    ("session_idle_days", 1, 30),
    ("session_absolute_days", 1, 90),
    ("login_account_failures", 3, 20),
    ("login_address_failures", 10, 200),
    ("block_first_minutes", 1, 15),
    ("block_max_minutes", 15, 120),
]


@pytest.mark.parametrize(("name", "low", "high"), EDGES)
def test_each_bound_holds_at_its_edge_and_fails_one_past_it(name: str, low: int, high: int) -> None:
    assert ss.BOUNDS[name] == (low, high)
    # Edges are tried with the other fields chosen so that no cross rule interferes.
    base = with_(session_absolute_days=90, login_account_failures=3, login_address_failures=200)
    for inside in (low, high):
        assert [p for p in ss.problems({**base, name: inside}) if p.field == name] == []
    for outside in (low - 1, high + 1):
        assert ss.Problem(name, ss.RANGE) in ss.problems({**base, name: outside})


@pytest.mark.parametrize("bad", ["7", 7.0, None, True, [7]])
def test_a_value_of_the_wrong_type_is_a_problem(bad: object) -> None:
    assert ss.Problem("session_idle_days", ss.RANGE) in ss.problems(with_(session_idle_days=bad))


def test_a_missing_field_is_a_problem() -> None:
    values = dict(VALID)
    del values["block_max_minutes"]
    assert ss.problems(values) == [ss.Problem("block_max_minutes", ss.RANGE)]


def test_idle_cannot_exceed_absolute() -> None:
    assert ss.problems(with_(session_idle_days=10, session_absolute_days=10)) == []
    assert ss.problems(with_(session_idle_days=11, session_absolute_days=10)) == [
        ss.Problem("session_idle_days", ss.IDLE_OVER_ABSOLUTE)
    ]


def test_the_address_threshold_is_at_least_twice_the_account_one() -> None:
    assert ss.problems(with_(login_account_failures=15, login_address_failures=30)) == []
    assert ss.problems(with_(login_account_failures=16, login_address_failures=31)) == [
        ss.Problem("login_address_failures", ss.ADDRESS_UNDER_ACCOUNT)
    ]


# --- the environment's values ----------------------------------------------------------------


def test_at_the_defaults_nothing_is_adjusted() -> None:
    values, adjusted = ss.from_environment(Settings())
    assert (values, adjusted) == (ss.DEFAULTS, [])


def test_an_absolute_duration_above_90_is_read_as_90_and_reported() -> None:
    values, adjusted = ss.from_environment(Settings(session_absolute_days=365))
    assert values.session_absolute_days == 90 and values.session_idle_days == 7
    assert adjusted == ["PT_SESSION_ABSOLUTE_DAYS"]


def test_an_idle_duration_above_30_is_read_as_30_and_reported() -> None:
    values, adjusted = ss.from_environment(Settings(session_idle_days=45))
    assert values.session_idle_days == 30
    assert adjusted == ["PT_SESSION_IDLE_DAYS"]


def test_idle_above_absolute_becomes_absolute_and_is_reported() -> None:
    values, adjusted = ss.from_environment(Settings(session_idle_days=20, session_absolute_days=10))
    assert (values.session_idle_days, values.session_absolute_days) == (10, 10)
    assert adjusted == ["PT_SESSION_IDLE_DAYS"]


def test_a_duration_below_1_is_read_as_1_and_never_stops_the_start() -> None:
    values, adjusted = ss.from_environment(Settings(session_idle_days=0, session_absolute_days=-3))
    assert (values.session_idle_days, values.session_absolute_days) == (1, 1)
    assert adjusted == ["PT_SESSION_IDLE_DAYS", "PT_SESSION_ABSOLUTE_DAYS"]


def test_the_values_from_the_environment_are_always_valid() -> None:
    for idle, absolute in [(0, 0), (99, 5), (31, 365), (1, 1), (30, 90)]:
        values, _ = ss.from_environment(
            Settings(session_idle_days=idle, session_absolute_days=absolute)
        )
        assert ss.problems(vars_of(values)) == []


def vars_of(values: ss.SecuritySettings) -> dict[str, object]:
    return {name: getattr(values, name) for name in ss.BOUNDS}


# --- the policies ----------------------------------------------------------------------------


def test_the_session_policy_is_in_days() -> None:
    values = ss.SecuritySettings(session_idle_days=3, session_absolute_days=40)
    assert ss.session_policy(values) == SessionPolicy(timedelta(days=3), timedelta(days=40))


def test_the_memory_follows_the_longest_block() -> None:
    assert ss.throttle_policy(ss.DEFAULTS).forget_after == timedelta(minutes=30)
    long = ss.throttle_policy(ss.SecuritySettings(block_max_minutes=120))
    assert long.forget_after == timedelta(minutes=240)
    assert ss.throttle_policy(ss.SecuritySettings(block_max_minutes=15)).forget_after == timedelta(
        minutes=30
    )


def test_the_thresholds_and_blocks_reach_the_keys_and_the_code_guesses_keep_their_rules() -> None:
    policy = ss.throttle_policy(
        ss.SecuritySettings(
            login_account_failures=8,
            login_address_failures=90,
            block_first_minutes=3,
            block_max_minutes=60,
        )
    )
    assert dict(policy.free_failures) == {USER: 8, PASSWORD_CHANGE: 8, IP: 90, TOTP: 5}
    assert (policy.first_block, policy.max_block) == (timedelta(minutes=3), timedelta(minutes=60))
    assert dict(policy.forget_after_by_kind) == {TOTP: timedelta(hours=24)}
    assert policy.cleared_on_success == ThrottlePolicy().cleared_on_success


# --- the throttle with a long maximum block ----------------------------------------------------


def fail(throttle: LoginThrottle, keys: list[str], at: datetime) -> None:
    with throttle.attempt(keys, at) as attempt:
        assert not attempt.refused


def test_a_long_block_does_not_end_with_its_count_erased() -> None:
    """With a 30-minute memory, a 32-minute block ended with the count forgotten, so the
    doubling started again from the first block and the longer cap slowed nothing."""
    values = ss.SecuritySettings(block_max_minutes=60)
    throttle = LoginThrottle(ss.throttle_policy(values))
    keys = login_keys("alice", None)
    now = T0
    blocks = []
    for _ in range(5):  # five free failures
        fail(throttle, keys, now)
    # Blocks of 1, 2, 4, 8, 16, 32 minutes: fail again each time a block has just ended.
    for expected in (1, 2, 4, 8, 16, 32):
        state = throttle._states[keys[0]]
        assert state.blocked_until is not None
        blocks.append((state.blocked_until - now) / timedelta(minutes=1))
        assert blocks[-1] == expected
        now = state.blocked_until
        assert not refused_probe(throttle, keys, now)
        fail(throttle, keys, now)
    # The 32-minute block ended; the next block is 60 (the cap), not the first block again.
    state = throttle._states[keys[0]]
    assert state.blocked_until is not None
    assert (state.blocked_until - now) == timedelta(minutes=60)


def refused_probe(throttle: LoginThrottle, keys: list[str], at: datetime) -> bool:
    attempt = throttle._reserve(keys, at)
    if not attempt.refused:
        with throttle._lock:
            for key in keys:
                throttle._states[key].in_flight -= 1
    return attempt.refused


def test_the_count_survives_a_32_minute_block_at_a_cap_of_60_but_not_at_the_default_policy() -> (
    None
):
    keys = login_keys("bob", None)

    def after_a_32_minute_block(policy: ThrottlePolicy) -> bool:
        throttle = LoginThrottle(policy)
        now = T0
        for _ in range(5):
            fail(throttle, keys, now)
        for _ in range(5):  # blocks 1, 2, 4, 8, 16
            now = throttle._states[keys[0]].blocked_until  # type: ignore[assignment]
            fail(throttle, keys, now)
        end = throttle._states[keys[0]].blocked_until  # a 32 or 15 minute block
        assert end is not None
        return throttle._current(keys[0], end + timedelta(seconds=1)) is not None

    long_policy = ss.throttle_policy(ss.SecuritySettings(block_max_minutes=60))
    assert after_a_32_minute_block(long_policy) is True
    # The old behaviour (a fixed 30-minute memory with a 60-minute cap) forgets the count.
    legacy = ThrottlePolicy(max_block=timedelta(minutes=60), forget_after=timedelta(minutes=30))
    assert after_a_32_minute_block(legacy) is False


# --- set_policy ------------------------------------------------------------------------------


def test_replacing_the_policy_keeps_counts_and_running_blocks() -> None:
    throttle = LoginThrottle()
    keys = login_keys("carol", "203.0.113.9")
    for _ in range(5):
        fail(throttle, keys, T0)  # blocked for a minute
    assert refused_probe(throttle, keys, T0 + timedelta(seconds=10))
    throttle.set_policy(ss.throttle_policy(ss.SecuritySettings(login_account_failures=20)))
    assert refused_probe(throttle, keys, T0 + timedelta(seconds=10))  # still blocked
    assert throttle._states[keys[0]].failures == 5


def test_a_lower_threshold_applies_at_the_next_failure() -> None:
    throttle = LoginThrottle(ss.throttle_policy(ss.SecuritySettings(login_account_failures=10)))
    keys = login_keys("dave", None)
    for _ in range(4):
        fail(throttle, keys, T0)
    assert not refused_probe(throttle, keys, T0)
    throttle.set_policy(ss.throttle_policy(ss.SecuritySettings(login_account_failures=3)))
    # Four failures already counted: the next one goes over the new threshold and blocks.
    fail(throttle, keys, T0)
    assert refused_probe(throttle, keys, T0 + timedelta(seconds=1))


def test_the_totp_key_keeps_its_threshold_of_five() -> None:
    throttle = LoginThrottle(ss.throttle_policy(ss.SecuritySettings(login_account_failures=20)))
    from uuid import uuid4

    keys = totp_keys(uuid4())
    for _ in range(5):
        fail(throttle, keys, T0)
    assert refused_probe(throttle, keys, T0 + timedelta(seconds=1))


# --- the daily cleanup and the start log -------------------------------------------------------


def test_the_daily_cleanup_uses_a_policy_replaced_after_the_start(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[SessionPolicy] = []

    def delete_expired(_session: object, _now: datetime, policy: SessionPolicy) -> int:
        seen.append(policy)
        return 0

    monkeypatch.setattr(market_startup.sessions, "delete_expired", delete_expired)
    run = market_startup.housekeeping(
        client.app.state.engine, lambda: client.app.state.session_policy, lambda: T0
    )
    run()
    replaced = SessionPolicy(timedelta(days=2), timedelta(days=3))
    client.app.state.session_policy = replaced
    run()
    assert seen[-1] == replaced and seen[0] != replaced


class Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def start_and_collect(tmp_path: Path, **environment: int) -> list[logging.LogRecord]:
    handler = Records()
    logger = logging.getLogger("portfolio_tracker.main")  # own handler: the start resets root's
    logger.addHandler(handler)
    try:
        app = create_app(Settings(data_dir=tmp_path / "data", **environment))
        with TestClient(app):
            pass
    finally:
        logger.removeHandler(handler)
    return [r for r in handler.records if "outside its range" in r.getMessage()]


def test_the_start_log_has_one_warning_per_adjusted_variable(tmp_path: Path) -> None:
    warnings = start_and_collect(tmp_path, session_idle_days=45, session_absolute_days=365)
    found = {(r.variable, r.value_used) for r in warnings}  # type: ignore[attr-defined]
    assert found == {("PT_SESSION_IDLE_DAYS", 30), ("PT_SESSION_ABSOLUTE_DAYS", 90)}
    assert len(warnings) == 2


def test_the_start_log_has_no_warning_at_the_defaults(tmp_path: Path) -> None:
    assert start_and_collect(tmp_path) == []


def test_the_adjustment_warning_names_no_user_address_or_secret(tmp_path: Path) -> None:
    (warning,) = start_and_collect(tmp_path, session_absolute_days=365)
    text = warning.getMessage() + repr(sorted(warning.__dict__.items()))
    for forbidden in ("password", "secret", "token", "127.0.0.1", "username"):
        assert forbidden not in text.lower()
