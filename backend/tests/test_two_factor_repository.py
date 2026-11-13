"""The two-factor rows (D-174): one secret per user, steps accepted once, recovery codes used
once, everything removed together. Invented users only."""

import threading
from datetime import UTC, datetime
from pathlib import Path

from helpers import details
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from portfolio_tracker.auth import recovery, totp
from portfolio_tracker.db import create_db_engine, db_transaction
from portfolio_tracker.db.models import RecoveryCode, TwoFactor, User
from portfolio_tracker.migrations_runner import run_migrations
from portfolio_tracker.repositories import two_factor, users

AT = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def user(session: Session, username: str) -> User:
    return users.add_user(
        session, username=username, **details(username), role="INVESTOR", password_hash="x",
        language="en",
    )  # fmt: skip


def enrolled(session: Session, username: str) -> tuple[User, list[str]]:
    who = user(session, username)
    two_factor.begin(session, who.id, totp.new_secret(), AT)
    two_factor.confirm(session, who.id, AT)
    codes = recovery.new_codes()
    two_factor.replace_recovery_codes(session, who.id, map(recovery.hash_code, codes), AT)
    return who, codes


def test_a_pending_secret_is_not_two_factor_authentication(db_session: Session) -> None:
    anna = user(db_session, "anna")
    assert two_factor.get(db_session, anna.id) is None

    row = two_factor.begin(db_session, anna.id, totp.new_secret(), AT)

    assert row.confirmed_at is None and row.last_step is None
    assert not two_factor.is_enabled(db_session, anna.id)
    assert two_factor.confirmed(db_session, anna.id) is None
    assert two_factor.enabled_user_ids(db_session) == set()


def test_beginning_again_replaces_the_pending_secret(db_session: Session) -> None:
    anna = user(db_session, "anna")
    first = two_factor.begin(db_session, anna.id, totp.new_secret(), AT).secret

    second = two_factor.begin(db_session, anna.id, totp.new_secret(), AT).secret

    assert first != second
    assert db_session.scalars(select(TwoFactor.secret)).all() == [second]


def test_confirming_turns_it_on_for_that_user_only(db_session: Session) -> None:
    anna, _ = enrolled(db_session, "anna")
    bruno = user(db_session, "bruno")
    db_session.expire_all()

    assert two_factor.is_enabled(db_session, anna.id)
    assert two_factor.confirmed(db_session, anna.id).confirmed_at == AT
    assert not two_factor.is_enabled(db_session, bruno.id)
    assert two_factor.enabled_user_ids(db_session) == {anna.id}


def test_a_step_is_accepted_once_and_never_an_older_one(db_session: Session) -> None:
    anna, _ = enrolled(db_session, "anna")

    assert two_factor.accept_step(db_session, anna.id, 100)
    assert not two_factor.accept_step(db_session, anna.id, 100)
    assert not two_factor.accept_step(db_session, anna.id, 99)
    assert two_factor.accept_step(db_session, anna.id, 101)
    db_session.expire_all()
    assert two_factor.get(db_session, anna.id).last_step == 101


def test_a_step_of_a_user_without_a_secret_is_not_accepted(db_session: Session) -> None:
    assert not two_factor.accept_step(db_session, user(db_session, "anna").id, 100)


def test_of_two_requests_with_the_same_step_only_one_is_accepted(tmp_path: Path) -> None:
    """Two write transactions at once, each on its own connection (as two requests are)."""
    db_path = tmp_path / "db" / "app.sqlite"
    engine: Engine = create_db_engine(db_path)
    run_migrations(engine, db_path, tmp_path / "backups")
    with db_transaction(engine, write=True) as session:
        anna_id = enrolled(session, "anna")[0].id
    start = threading.Barrier(8)
    accepted: list[bool] = []

    def request() -> None:
        start.wait()
        with db_transaction(engine, write=True) as session:
            accepted.append(two_factor.accept_step(session, anna_id, 100))

    threads = [threading.Thread(target=request) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    engine.dispose()

    assert sorted(accepted) == [False] * 7 + [True]


def test_only_hashes_of_the_recovery_codes_are_stored(db_session: Session) -> None:
    anna, codes = enrolled(db_session, "anna")

    stored = db_session.scalars(select(RecoveryCode.code_hash)).all()

    assert sorted(stored) == sorted(recovery.hash_code(code) for code in codes)
    assert two_factor.recovery_codes_left(db_session, anna.id) == 10
    dump = "\n".join(db_session.connection().connection.driver_connection.iterdump())
    for code in codes:
        assert code not in dump and code.replace("-", "") not in dump


def test_a_recovery_code_is_used_once(db_session: Session) -> None:
    anna, codes = enrolled(db_session, "anna")
    unused = two_factor.unused_recovery_codes(db_session, anna.id)
    index = recovery.find(codes[3], [code_hash for _, code_hash in unused])
    code_id = unused[index][0]

    assert two_factor.use_recovery_code(db_session, code_id, AT)
    assert not two_factor.use_recovery_code(db_session, code_id, AT)

    left = two_factor.unused_recovery_codes(db_session, anna.id)
    assert two_factor.recovery_codes_left(db_session, anna.id) == len(left) == 9
    assert recovery.find(codes[3], [code_hash for _, code_hash in left]) is None


def test_new_recovery_codes_replace_all_the_old_ones(db_session: Session) -> None:
    anna, old = enrolled(db_session, "anna")
    code_id = two_factor.unused_recovery_codes(db_session, anna.id)[0][0]
    two_factor.use_recovery_code(db_session, code_id, AT)
    new = recovery.new_codes()

    two_factor.replace_recovery_codes(db_session, anna.id, map(recovery.hash_code, new), AT)

    hashes = [code_hash for _, code_hash in two_factor.unused_recovery_codes(db_session, anna.id)]
    assert len(hashes) == 10
    assert all(recovery.find(code, hashes) is None for code in old)
    assert all(recovery.find(code, hashes) is not None for code in new)


def test_removing_deletes_the_secret_and_the_codes_of_that_user_only(db_session: Session) -> None:
    anna, _ = enrolled(db_session, "anna")
    bruno, _ = enrolled(db_session, "bruno")

    assert two_factor.remove(db_session, anna.id) is True
    assert two_factor.remove(db_session, anna.id) is False  # nothing left to remove

    assert two_factor.get(db_session, anna.id) is None
    assert two_factor.recovery_codes_left(db_session, anna.id) == 0
    assert two_factor.is_enabled(db_session, bruno.id)
    assert two_factor.recovery_codes_left(db_session, bruno.id) == 10


def test_removing_a_pending_enrolment_says_it_was_off(db_session: Session) -> None:
    anna = user(db_session, "anna")
    two_factor.begin(db_session, anna.id, totp.new_secret(), AT)

    assert two_factor.remove(db_session, anna.id) is False
    assert two_factor.get(db_session, anna.id) is None


def test_the_secret_is_not_in_a_row_s_representation(db_session: Session) -> None:
    anna = user(db_session, "anna")
    row = two_factor.begin(db_session, anna.id, totp.new_secret(), AT)

    assert row.secret not in repr(row) and row.secret not in str(row)
