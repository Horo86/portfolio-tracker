"""The intermediate proof of a two-step login (D-174): kept in memory as a hash, 5 minutes,
5 attempts, consumed only by a success."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from portfolio_tracker.auth import challenge
from portfolio_tracker.auth.challenge import LoginChallenges

T0 = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def test_a_proof_is_256_random_bits_and_only_its_hash_is_kept() -> None:
    store = LoginChallenges()
    user = uuid4()

    first = store.issue(user, "hash", False, T0)
    second = store.issue(user, "hash", False, T0)

    assert first != second and len(first) >= 43  # 32 bytes, URL-safe base64
    assert first not in store._entries and second not in store._entries
    assert all(len(key) == 64 for key in store._entries)
    assert first not in repr(store._entries)


def test_a_proof_carries_what_the_second_step_needs() -> None:
    store = LoginChallenges()
    user = uuid4()

    pending = store.peek(store.issue(user, "the-hash", True, T0), T0)

    assert (pending.user_id, pending.password_hash, pending.issue_token) == (user, "the-hash", True)
    assert pending.expires_at == T0 + timedelta(minutes=5)


def test_a_proof_expires_after_five_minutes() -> None:
    store = LoginChallenges()
    proof = store.issue(uuid4(), "hash", False, T0)

    assert store.peek(proof, T0 + timedelta(minutes=4, seconds=59)) is not None
    assert store.peek(proof, T0 + timedelta(minutes=5)) is None
    assert store.take_attempt(proof, T0 + timedelta(minutes=5)) is None
    assert len(store) == 0  # forgotten, not just refused


def test_an_unknown_proof_is_nothing() -> None:
    store = LoginChallenges()
    store.issue(uuid4(), "hash", False, T0)

    assert store.peek("not-a-proof", T0) is None and store.take_attempt("", T0) is None


def test_a_proof_allows_five_attempts_and_looking_at_it_uses_none() -> None:
    store = LoginChallenges()
    proof = store.issue(uuid4(), "hash", False, T0)
    for _ in range(20):
        assert store.peek(proof, T0) is not None

    left = [store.take_attempt(proof, T0)[1] for _ in range(challenge.MAX_ATTEMPTS)]

    assert left == [4, 3, 2, 1, 0]
    assert store.take_attempt(proof, T0) is None and store.peek(proof, T0) is None


def test_a_success_consumes_the_proof() -> None:
    store = LoginChallenges()
    proof = store.issue(uuid4(), "hash", False, T0)
    store.take_attempt(proof, T0)

    store.consume(proof)
    store.consume(proof)  # twice is harmless

    assert store.peek(proof, T0) is None


def test_the_pending_logins_of_a_user_can_be_dropped() -> None:
    store = LoginChallenges()
    anna, bruno = uuid4(), uuid4()
    mine = [store.issue(anna, "hash", False, T0) for _ in range(2)]
    other = store.issue(bruno, "hash", False, T0)

    assert store.drop_user(anna) == 2

    assert all(store.peek(proof, T0) is None for proof in mine)
    assert store.peek(other, T0) is not None


def test_the_number_of_pending_logins_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(challenge, "MAX_PENDING", 3)
    store = LoginChallenges()
    proofs = [store.issue(uuid4(), "hash", False, T0) for _ in range(5)]

    assert len(store) == 3
    assert [store.peek(proof, T0) is not None for proof in proofs] == [
        False,
        False,
        True,
        True,
        True,
    ]


def test_expired_proofs_are_forgotten_when_a_new_one_is_issued() -> None:
    store = LoginChallenges()
    for _ in range(4):
        store.issue(uuid4(), "hash", False, T0)

    store.issue(uuid4(), "hash", False, T0 + timedelta(minutes=6))

    assert len(store) == 1
