"""Provider credentials encrypted at rest (D-21c, D-124).

AES-GCM from `cryptography`, with a 32-byte key and a random 12-byte nonce for every value
stored. The associated data names the provider, so a value copied to another provider's row
does not decrypt. The key is `PT_SECRET_KEY` (base64 of 32 bytes) or, when that is unset or
empty, the file `<data dir>/secrets/secret.key` (the same base64 text), generated at the first
start with mode 0600 in a 0700 directory and kept afterwards.

The key and the credentials never appear in a log line, an error message or a repr: errors
name only the source of a key (the variable or the file path) and the provider of a value.
The storage of encrypted values is in `repositories/credentials.py`.
"""

import base64
import binascii
import logging
import os
import stat
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from portfolio_tracker.marketdata.routing import NAME

log = logging.getLogger(__name__)

KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # the GCM standard nonce
MAX_VALUE_CHARS = 4096
KEY_ENV = "PT_SECRET_KEY"
KEY_FILE = Path("secrets") / "secret.key"  # relative to the data directory
_AAD_PREFIX = b"portfolio-tracker provider credential v1:"


class SecretKeyError(Exception):
    """The key cannot be used. The message names its source, never its content."""


class CredentialUnreadable(Exception):
    """A stored credential does not decrypt: another key, or altered data."""

    def __init__(self, provider: str) -> None:
        super().__init__(
            f"the credential of {provider} cannot be read: another key or altered data"
        )
        self.provider = provider


class SecretKey:
    """The 32-byte key, with a repr that shows only where it came from."""

    __slots__ = ("_value", "source")

    def __init__(self, value: bytes, source: str) -> None:
        if len(value) != KEY_BYTES:
            raise SecretKeyError(f"{source}: the key must be {KEY_BYTES} bytes")
        self._value = value
        self.source = source

    def reveal(self) -> bytes:
        return self._value

    def __repr__(self) -> str:
        return f"SecretKey(source={self.source!r})"


def _decode(text: bytes, source: str) -> bytes:
    try:
        return base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError):
        raise SecretKeyError(f"{source}: the key is not base64") from None


def load_key(env_value: str | None, data_dir: Path) -> SecretKey:
    """The key from `PT_SECRET_KEY` if set, otherwise from the key file, created if missing."""
    if env_value and env_value.strip():
        return SecretKey(_decode(env_value.encode(), KEY_ENV), KEY_ENV)
    path = data_dir / KEY_FILE
    try:
        return _read_key_file(path)
    except FileNotFoundError:
        pass
    _create_key_file(path)
    return _read_key_file(path)


def _read_key_file(path: Path) -> SecretKey:
    mode = path.stat().st_mode  # FileNotFoundError: not created yet
    if not stat.S_ISREG(mode):
        raise SecretKeyError(f"{path}: not a regular file")
    if mode & 0o077:
        try:
            path.chmod(0o600)
            log.warning("secret key file permissions set to 0600", extra={"path": str(path)})
        except OSError:
            log.warning(
                "secret key file is accessible to other users; set its mode to 0600",
                extra={"path": str(path)},
            )
    try:
        text = path.read_bytes()
    except OSError as error:
        raise SecretKeyError(f"{path}: cannot be read ({type(error).__name__})") from None
    return SecretKey(_decode(text, str(path)), str(path))


def _create_key_file(path: Path) -> None:
    """Write a new key to a temporary file, then link it into place: a partial write never
    becomes the key, and two processes starting together end up with the same key."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)  # left by a crash (in a container the pid repeats)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(base64.b64encode(os.urandom(KEY_BYTES)) + b"\n")
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return  # another process won: its key is used
    finally:
        temporary.unlink(missing_ok=True)
    log.info("secret key generated", extra={"path": str(path)})


def _check_provider(provider: str) -> bytes:
    if not NAME.fullmatch(provider):
        raise ValueError("not an engine name")
    return _AAD_PREFIX + provider.encode()


def _check_value(value: str) -> bytes:
    if not 0 < len(value) <= MAX_VALUE_CHARS or any(
        ord(char) < 0x20 or ord(char) == 0x7F for char in value
    ):
        raise ValueError(f"a credential is 1 to {MAX_VALUE_CHARS} characters, no control ones")
    return value.encode()


class CredentialCipher:
    """Encrypts and decrypts one provider's credential with the instance key."""

    def __init__(self, key: SecretKey) -> None:
        self._aead = AESGCM(key.reveal())
        self._source = key.source

    def encrypt(self, provider: str, value: str) -> tuple[bytes, bytes]:
        """A fresh nonce and the ciphertext (with its tag) of `value` for `provider`."""
        aad = _check_provider(provider)
        nonce = os.urandom(NONCE_BYTES)
        return nonce, self._aead.encrypt(nonce, _check_value(value), aad)

    def decrypt(self, provider: str, nonce: bytes, ciphertext: bytes) -> str:
        aad = _check_provider(provider)
        if len(nonce) != NONCE_BYTES:
            raise CredentialUnreadable(provider)
        try:
            return self._aead.decrypt(nonce, ciphertext, aad).decode()
        except (InvalidTag, UnicodeDecodeError):
            raise CredentialUnreadable(provider) from None

    def __repr__(self) -> str:
        return f"CredentialCipher(key from {self._source!r})"
