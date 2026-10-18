"""Credential store (D-124): the key, AES-GCM with the provider as associated data, the
stored rows, and that neither the key nor a value ever reaches a log or an error message.
Every value and key here is invented or random."""

import base64
import errno
import logging
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from portfolio_tracker.backup import BACKUP_EXCLUDED
from portfolio_tracker.config import Settings
from portfolio_tracker.credentials import (
    KEY_BYTES,
    KEY_FILE,
    MAX_VALUE_CHARS,
    NONCE_BYTES,
    CredentialCipher,
    CredentialUnreadable,
    SecretKey,
    SecretKeyError,
    load_key,
)
from portfolio_tracker.db.models import ProviderCredential
from portfolio_tracker.main import create_app
from portfolio_tracker.repositories import credentials as store

FAKE = "fake-credential-0123456789-not-a-real-key"  # invented


def random_key(source: str = "test") -> SecretKey:
    return SecretKey(os.urandom(KEY_BYTES), source)


def encoded(key: SecretKey) -> str:
    return base64.b64encode(key.reveal()).decode()


@pytest.fixture
def cipher() -> CredentialCipher:
    return CredentialCipher(random_key())


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# --- the key ----------------------------------------------------------------------------------


def test_a_key_file_is_generated_once_with_mode_0600(tmp_path: Path) -> None:
    key = load_key(None, tmp_path)
    path = tmp_path / KEY_FILE

    assert mode(path) == 0o600 and mode(path.parent) == 0o700
    assert base64.b64decode(path.read_bytes().strip(), validate=True) == key.reveal()
    assert load_key(None, tmp_path).reveal() == key.reveal()  # kept afterwards
    assert load_key("", tmp_path).reveal() == key.reveal()  # empty variable: unset
    assert [p.name for p in path.parent.iterdir()] == ["secret.key"]  # no temporary left


def test_the_variable_wins_and_no_file_is_written(tmp_path: Path) -> None:
    wanted = random_key()
    key = load_key(f"  {encoded(wanted)}\n", tmp_path)
    assert key.reveal() == wanted.reveal() and key.source == "PT_SECRET_KEY"
    assert not (tmp_path / KEY_FILE).exists()


@pytest.mark.parametrize(
    "value",
    [
        "not base64 at all!",
        base64.b64encode(b"short").decode(),
        base64.b64encode(os.urandom(KEY_BYTES + 1)).decode(),
    ],
)
def test_a_bad_variable_stops_without_showing_it(tmp_path: Path, value: str) -> None:
    with pytest.raises(SecretKeyError) as error:
        load_key(value, tmp_path)
    assert "PT_SECRET_KEY" in str(error.value)
    assert value not in str(error.value) and value not in repr(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None or error.value.__suppress_context__


def test_a_bad_key_file_stops_without_showing_it(tmp_path: Path) -> None:
    path = tmp_path / KEY_FILE
    path.parent.mkdir()
    path.write_text("bad content\n")
    path.chmod(0o600)
    with pytest.raises(SecretKeyError, match="not base64") as error:
        load_key(None, tmp_path)
    assert "bad content" not in str(error.value)


def test_a_key_path_that_is_a_folder_is_refused(tmp_path: Path) -> None:
    (tmp_path / KEY_FILE).mkdir(parents=True)
    with pytest.raises(SecretKeyError, match="not a regular file"):
        load_key(None, tmp_path)


def test_an_open_key_file_is_set_to_0600(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    key = load_key(None, tmp_path)
    path = tmp_path / KEY_FILE
    path.chmod(0o644)
    with caplog.at_level(logging.WARNING):
        assert load_key(None, tmp_path).reveal() == key.reveal()
    assert mode(path) == 0o600
    assert "permissions set to 0600" in caplog.text


def test_a_key_file_whose_mode_cannot_be_changed_only_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """On some network shares chmod fails: the app warns and goes on with the key."""
    key = load_key(None, tmp_path)
    (tmp_path / KEY_FILE).chmod(0o644)

    def refuse(self: Path, mode: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(Path, "chmod", refuse)
    with caplog.at_level(logging.WARNING):
        assert load_key(None, tmp_path).reveal() == key.reveal()
    assert "accessible to other users; set its mode to 0600" in caplog.text


def test_a_share_without_hard_links_still_gets_a_key_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_links(source: object, target: object) -> None:
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_links)
    key = load_key(None, tmp_path)
    path = tmp_path / KEY_FILE
    assert mode(path) == 0o600 and [p.name for p in path.parent.iterdir()] == ["secret.key"]
    assert load_key(None, tmp_path).reveal() == key.reveal()


def test_a_stale_temporary_file_does_not_block_the_first_start(tmp_path: Path) -> None:
    folder = tmp_path / KEY_FILE.parent
    folder.mkdir()
    (folder / f".secret.key.{os.getpid()}.tmp").write_bytes(b"half")
    load_key(None, tmp_path)
    assert [p.name for p in folder.iterdir()] == ["secret.key"]


def test_a_failed_write_leaves_no_half_written_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review L4: on a share without hard links the key is written in place; a write that
    fails (disk full, the container killed) must not leave a file that stops every start."""

    def no_links(source: object, target: object) -> None:
        raise OSError(errno.EPERM, "Operation not permitted")

    synced: list[int] = []
    real_fsync = os.fsync

    def disk_full_on_the_second_write(descriptor: int) -> None:
        synced.append(descriptor)
        if len(synced) == 2:  # the temporary file is fine; the key file in place is not
            raise OSError(errno.ENOSPC, "No space left on device")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "link", no_links)
    monkeypatch.setattr(os, "fsync", disk_full_on_the_second_write)
    with pytest.raises(OSError):
        load_key(None, tmp_path)
    assert len(synced) == 2 and list((tmp_path / KEY_FILE.parent).iterdir()) == []

    monkeypatch.undo()
    key = load_key(None, tmp_path)  # the next start creates a whole key
    assert load_key(None, tmp_path).reveal() == key.reveal()


def test_stale_copies_of_the_key_are_removed_at_start(tmp_path: Path) -> None:
    """Review L5: a start killed between linking and cleaning leaves a full copy of the key
    under another process id; it must not stay beside the key (nor reach a backup)."""
    key = load_key(None, tmp_path)
    folder = tmp_path / KEY_FILE.parent
    (folder / ".secret.key.99999.tmp").write_bytes((folder / "secret.key").read_bytes())
    assert load_key(None, tmp_path).reveal() == key.reveal()
    assert [p.name for p in folder.iterdir()] == ["secret.key"]


def test_a_chmod_without_effect_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review L6: some SMB mounts accept chmod and keep the mode."""
    load_key(None, tmp_path)
    (tmp_path / KEY_FILE).chmod(0o644)
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)
    with caplog.at_level(logging.WARNING):
        load_key(None, tmp_path)
    assert "file is accessible to other users; set its mode to 0600" in caplog.text
    assert "permissions set" not in caplog.text


def test_an_open_key_folder_is_set_to_0700(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    load_key(None, tmp_path)
    folder = tmp_path / KEY_FILE.parent
    folder.chmod(0o755)
    with caplog.at_level(logging.WARNING):
        load_key(None, tmp_path)
    assert mode(folder) == 0o700 and "folder permissions set to 0700" in caplog.text


def test_a_link_to_a_missing_key_file_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / KEY_FILE.parent
    folder.mkdir()
    (folder / "secret.key").symlink_to(tmp_path / "elsewhere.key")
    with pytest.raises(SecretKeyError, match="does not exist"):
        load_key(None, tmp_path)
    assert not (tmp_path / "elsewhere.key").exists()  # nothing created through the link


def test_reprs_show_only_the_source(tmp_path: Path) -> None:
    key = random_key("PT_SECRET_KEY")
    settings = Settings(data_dir=tmp_path, secret_key=encoded(key))
    for text in (repr(key), repr(CredentialCipher(key)), repr(settings), str(settings)):
        assert encoded(key) not in text and key.reveal().hex() not in text


# --- encryption -------------------------------------------------------------------------------


def test_a_value_round_trips_with_a_fresh_nonce(cipher: CredentialCipher) -> None:
    first = cipher.encrypt("keyed", FAKE)
    second = cipher.encrypt("keyed", FAKE)
    assert len(first[0]) == NONCE_BYTES and first[0] != second[0] and first[1] != second[1]
    assert FAKE.encode() not in first[1]
    assert cipher.decrypt("keyed", *first) == FAKE


def test_another_key_cannot_read_it(cipher: CredentialCipher) -> None:
    nonce, ciphertext = cipher.encrypt("keyed", FAKE)
    with pytest.raises(CredentialUnreadable) as error:
        CredentialCipher(random_key()).decrypt("keyed", nonce, ciphertext)
    assert error.value.provider == "keyed" and FAKE not in str(error.value)


def flip(data: bytes, index: int) -> bytes:
    return data[:index] + bytes([data[index] ^ 1]) + data[index + 1 :]


@pytest.mark.parametrize(
    "change",
    [
        lambda n, c: (flip(n, 0), c),  # nonce altered
        lambda n, c: (n[:-1], c),  # nonce shortened
        lambda n, c: (n, flip(c, 0)),  # ciphertext altered
        lambda n, c: (n, flip(c, len(c) - 1)),  # tag altered
        lambda n, c: (n, c[:-1]),  # truncated
        lambda n, c: (n, b""),
    ],
)
def test_altered_data_is_refused(cipher: CredentialCipher, change) -> None:
    nonce, ciphertext = change(*cipher.encrypt("keyed", FAKE))
    with pytest.raises(CredentialUnreadable):
        cipher.decrypt("keyed", nonce, ciphertext)


def test_a_value_moved_to_another_provider_is_refused(cipher: CredentialCipher) -> None:
    nonce, ciphertext = cipher.encrypt("keyed", FAKE)
    with pytest.raises(CredentialUnreadable):
        cipher.decrypt("other", nonce, ciphertext)


@pytest.mark.parametrize("value", ["", "x" * (MAX_VALUE_CHARS + 1), "line\nbreak", "tab\there"])
def test_a_bad_value_is_refused_without_showing_it(cipher: CredentialCipher, value: str) -> None:
    with pytest.raises(ValueError) as error:
        cipher.encrypt("keyed", value)
    assert value == "" or value not in str(error.value)


@pytest.mark.parametrize("provider", ["", "Keyed", "a b", "x" * 33])
def test_a_bad_provider_name_is_refused(cipher: CredentialCipher, provider: str) -> None:
    with pytest.raises(ValueError, match="not an engine name"):
        cipher.encrypt(provider, FAKE)


# --- the stored rows --------------------------------------------------------------------------


def test_set_get_replace_and_delete(db_session: Session, cipher: CredentialCipher) -> None:
    assert store.get_credential(db_session, cipher, "keyed") is None
    assert store.credential_updated_at(db_session, "keyed") is None

    store.set_credential(db_session, cipher, "keyed", FAKE)
    row = db_session.get(ProviderCredential, "keyed")
    assert row is not None and FAKE.encode() not in row.ciphertext
    first_nonce = row.nonce
    assert store.get_credential(db_session, cipher, "keyed") == FAKE
    assert store.credential_updated_at(db_session, "keyed") is not None

    store.set_credential(db_session, cipher, "keyed", FAKE + "-2")
    assert store.get_credential(db_session, cipher, "keyed") == FAKE + "-2"
    assert db_session.get(ProviderCredential, "keyed").nonce != first_nonce

    assert store.delete_credential(db_session, "keyed") is True
    assert store.get_credential(db_session, cipher, "keyed") is None
    assert store.delete_credential(db_session, "keyed") is False


def test_a_lost_key_makes_stored_values_unreadable(
    tmp_path: Path, db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """The key file is deleted after a value was stored: a new key is generated at the next
    start, the old value no longer decrypts, and the engine counts as without credential."""
    store.set_credential(db_session, CredentialCipher(load_key(None, tmp_path)), "keyed", FAKE)
    (tmp_path / KEY_FILE).unlink()
    new_cipher = CredentialCipher(load_key(None, tmp_path))

    with pytest.raises(CredentialUnreadable):
        store.get_credential(db_session, new_cipher, "keyed")
    with caplog.at_level(logging.WARNING):
        assert store.credential_lookup(db_session, new_cipher)("keyed") is None
    assert "cannot be read" in caplog.text


def test_the_lookup_gives_the_registry_each_engine_its_value(
    db_session: Session, cipher: CredentialCipher
) -> None:
    store.set_credential(db_session, cipher, "keyed", FAKE)
    lookup = store.credential_lookup(db_session, cipher)
    assert lookup("keyed") == FAKE and lookup("other") is None


# --- nothing in logs or errors ----------------------------------------------------------------


@pytest.fixture
def every_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    with caplog.at_level(logging.DEBUG):
        yield caplog


def logged(caplog: pytest.LogCaptureFixture) -> str:
    """Messages, arguments and extra fields of every record, as one text."""
    return caplog.text + "".join(repr(record.__dict__) for record in caplog.records)


def test_neither_the_key_nor_a_value_is_ever_logged(
    tmp_path: Path, db_session: Session, every_log: pytest.LogCaptureFixture
) -> None:
    key = load_key(None, tmp_path)  # generated: logged with its path
    (tmp_path / KEY_FILE).chmod(0o644)
    key = load_key(None, tmp_path)  # permissions fixed: logged
    cipher = CredentialCipher(key)
    store.set_credential(db_session, cipher, "keyed", FAKE)
    store.get_credential(db_session, cipher, "keyed")
    stranger = CredentialCipher(random_key())
    store.credential_lookup(db_session, stranger)("keyed")  # unreadable: logged
    errors = []
    for attempt in (
        lambda: stranger.decrypt("keyed", *cipher.encrypt("keyed", FAKE)),
        lambda: load_key(encoded(key)[:-4], tmp_path),
        lambda: cipher.encrypt("keyed", FAKE + "\n"),
    ):
        with pytest.raises(Exception) as error:  # any error: its text is inspected below
            attempt()
        errors.append(f"{error.value!s} {error.value!r}")
    store.delete_credential(db_session, "keyed")

    text = logged(every_log) + " ".join(errors)
    assert "secret key generated" in every_log.text and "cannot be read" in every_log.text
    assert FAKE not in text
    assert encoded(key) not in text and key.reveal().hex() not in text


# --- the app and backups ----------------------------------------------------------------------


def test_the_app_creates_the_key_at_its_first_start(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert isinstance(client.app.state.credential_cipher, CredentialCipher)
    assert mode(settings.data_dir / KEY_FILE) == 0o600


def test_the_app_does_not_start_with_a_bad_variable(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", secret_key="bad key")
    with pytest.raises(SecretKeyError, match="PT_SECRET_KEY"), TestClient(create_app(settings)):
        pass
    assert not (tmp_path / "data" / "db").exists()  # stopped before the database


def test_backups_leave_out_the_credentials_and_the_key() -> None:
    assert BACKUP_EXCLUDED.tables == (ProviderCredential.__tablename__,)
    assert BACKUP_EXCLUDED.paths == (KEY_FILE.parent,) == (Path("secrets"),)  # the folder
