"""Time-based one-time passwords (RFC 6238; D-174). Pure: no database, no web, no clock of
its own. The moment is always given by the caller, so tests choose it.

Codes are 6 digits, HMAC-SHA-1, 30-second steps; a code of the step before or after the
current one is accepted, for a phone or a server whose clock is a little off. The caller stores
the step of the last accepted code and passes it back: a code whose step is at or below it is
refused, so a code works once.

The secret is 160 random bits, shown to the user once as base32 text and as a QR code, and
stored plain (D-33). Nothing here logs, and nothing puts a secret or a code in an exception.
"""

import base64
import binascii
import secrets
from datetime import datetime, timedelta

import segno
from cryptography.hazmat.primitives import constant_time
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.twofactor.totp import TOTP

DIGITS = 6
STEP_SECONDS = 30
TOLERANCE_STEPS = 1  # either side of the current step
SECRET_BYTES = 20  # 160 bits, the SHA-1 block RFC 4226 recommends
ISSUER = "Portfolio Tracker"
# A secret shown but not confirmed can be confirmed for this long, then the setup starts
# again; the daily housekeeping deletes the ones left for a day (D-174).
PENDING_LIFETIME = timedelta(minutes=15)
PENDING_KEPT = timedelta(days=1)


def new_secret() -> str:
    """A new secret as base32 text (32 characters, no padding)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii")


def _totp(secret: str) -> TOTP:
    try:
        key = base64.b32decode(secret, casefold=True)
    except (binascii.Error, ValueError):
        raise ValueError("not a base32 secret") from None  # never quotes the secret
    return TOTP(key, DIGITS, SHA1(), STEP_SECONDS)  # SHA-1 is RFC 6238's default


def step_of(at: datetime) -> int:
    """The 30-second step `at` falls in (`at` is timezone-aware)."""
    return int(at.timestamp()) // STEP_SECONDS


def code_at(secret: str, step: int) -> str:
    """The code of a step: for the tests and the end-to-end helper's reference."""
    return _totp(secret).generate(step * STEP_SECONDS).decode("ascii")


def normalize_code(text: str) -> str | None:
    """The six digits of what was typed (spaces ignored), or None when it is not a code."""
    code = "".join(text.split())
    return code if len(code) == DIGITS and code.isascii() and code.isdigit() else None


def matching_step(secret: str, typed: str, at: datetime, last_step: int | None) -> int | None:
    """The step whose code `typed` is, among the current step and its neighbours, when that
    step is newer than `last_step`; else None (wrong, too old, too new, or already used).

    Every step of the window is generated and compared in constant time, whatever the
    earlier ones gave, so the time taken says nothing about which step matched."""
    code = normalize_code(typed)
    candidate = (code or "0" * DIGITS).encode("ascii")
    totp = _totp(secret)
    current = step_of(at)
    found: int | None = None
    for step in range(current - TOLERANCE_STEPS, current + TOLERANCE_STEPS + 1):
        if constant_time.bytes_eq(totp.generate(step * STEP_SECONDS), candidate):
            found = step  # the newest matching step
    if code is None or found is None:
        return None
    if last_step is not None and found <= last_step:
        return None
    return found


def provisioning_uri(secret: str, account: str) -> str:
    """The `otpauth://` address an authenticator app reads from the QR code."""
    return _totp(secret).get_provisioning_uri(account, ISSUER)


def qr_matrix(text: str) -> list[str]:
    """The QR code of `text` as rows of `0` and `1` (1 is a dark module), without the quiet
    zone: the page draws it as an inline SVG, so no markup is sent or injected (D-163)."""
    code = segno.make(text, error="m", micro=False)
    return ["".join("1" if module else "0" for module in row) for row in code.matrix]
