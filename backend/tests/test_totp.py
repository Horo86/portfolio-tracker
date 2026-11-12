"""TOTP (RFC 6238) as the app uses it (D-174): 6 digits, SHA-1, 30-second steps, one step of
tolerance either side, no reuse. Invented secrets only."""

import base64
import inspect
from datetime import UTC, datetime, timedelta

import pytest

from portfolio_tracker.auth import recovery, totp

# RFC 6238, appendix B: the SHA-1 test secret (the ASCII text "12345678901234567890") and its
# codes. The RFC prints 8 digits; a 6-digit code is the same value modulo 10^6.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()
RFC_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


def moment(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


NOW = moment(1_700_000_010)  # ten seconds into its step
STEP = totp.step_of(NOW)


@pytest.mark.parametrize(("seconds", "eight_digits"), RFC_VECTORS)
def test_codes_are_those_of_rfc_6238(seconds: int, eight_digits: str) -> None:
    at = moment(seconds)

    assert totp.code_at(RFC_SECRET, totp.step_of(at)) == eight_digits[-6:]
    assert totp.matching_step(RFC_SECRET, eight_digits[-6:], at, None) == seconds // 30


def test_a_secret_is_160_random_bits_in_base32() -> None:
    first, second = totp.new_secret(), totp.new_secret()

    assert first != second
    assert len(first) == 32 and len(base64.b32decode(first)) == 20


def test_secrets_and_recovery_codes_come_from_the_secrets_module_never_random() -> None:
    for module in (totp, recovery):
        source = inspect.getsource(module)
        assert "import secrets" in source
        assert "import random" not in source and "from random" not in source


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_the_current_step_and_one_either_side_are_accepted(offset: int) -> None:
    secret = totp.new_secret()

    assert (
        totp.matching_step(secret, totp.code_at(secret, STEP + offset), NOW, None) == STEP + offset
    )


@pytest.mark.parametrize("offset", [-3, -2, 2, 3])
def test_two_steps_away_is_refused(offset: int) -> None:
    secret = totp.new_secret()

    assert totp.matching_step(secret, totp.code_at(secret, STEP + offset), NOW, None) is None


def test_the_window_follows_the_clock_to_the_second() -> None:
    secret = totp.new_secret()
    code = totp.code_at(secret, STEP)
    start = moment(STEP * 30)

    assert totp.matching_step(secret, code, start - timedelta(seconds=30), None) == STEP
    assert totp.matching_step(secret, code, start - timedelta(seconds=31), None) is None
    assert totp.matching_step(secret, code, start + timedelta(seconds=59), None) == STEP
    assert totp.matching_step(secret, code, start + timedelta(seconds=60), None) is None


def test_a_code_at_or_below_the_last_used_step_is_refused() -> None:
    secret = totp.new_secret()

    assert totp.matching_step(secret, totp.code_at(secret, STEP), NOW, STEP) is None  # reused
    assert totp.matching_step(secret, totp.code_at(secret, STEP - 1), NOW, STEP) is None  # older
    assert totp.matching_step(secret, totp.code_at(secret, STEP + 1), NOW, STEP) == STEP + 1
    assert totp.matching_step(secret, totp.code_at(secret, STEP), NOW, STEP - 1) == STEP


def test_another_secret_s_code_is_refused() -> None:
    secret, other = totp.new_secret(), totp.new_secret()

    assert totp.matching_step(secret, totp.code_at(other, STEP), NOW, None) is None


@pytest.mark.parametrize("typed", ["", "12345", "1234567", "12345a", "١٢٣٤٥٦", "-12345", "12 34"])
def test_what_is_not_six_ascii_digits_is_never_a_code(typed: str) -> None:
    assert totp.normalize_code(typed) is None
    assert totp.matching_step(totp.new_secret(), typed, NOW, None) is None


def test_spaces_in_a_typed_code_are_ignored() -> None:
    secret = totp.new_secret()
    code = totp.code_at(secret, STEP)

    assert totp.matching_step(secret, f" {code[:3]} {code[3:]} ", NOW, None) == STEP


def test_the_check_compares_every_step_in_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """All three steps are compared with the library's constant-time comparison, also after a
    match: no `==` on a code and no early exit."""
    compared: list[bytes] = []
    real = totp.constant_time.bytes_eq

    def spy(left: bytes, right: bytes) -> bool:
        compared.append(left)
        return real(left, right)

    monkeypatch.setattr(totp.constant_time, "bytes_eq", spy)
    secret = totp.new_secret()

    assert totp.matching_step(secret, totp.code_at(secret, STEP - 1), NOW, None) == STEP - 1
    assert compared == [totp.code_at(secret, STEP + o).encode() for o in (-1, 0, 1)]
    compared.clear()
    assert totp.matching_step(secret, "not a code", NOW, None) is None
    assert len(compared) == 3  # the same work for something that is not a code


def test_a_bad_secret_is_an_error_that_does_not_quote_it() -> None:
    with pytest.raises(ValueError, match="not a base32 secret") as caught:
        totp.code_at("not/base32!", STEP)

    assert "not/base32!" not in str(caught.value) and caught.value.__cause__ is None


def test_the_address_for_the_authenticator_app_names_the_app_and_the_account() -> None:
    secret = totp.new_secret()
    uri = totp.provisioning_uri(secret, "anna@example.test")

    assert uri.startswith("otpauth://totp/Portfolio%20Tracker:anna%40example.test?")
    for part in (f"secret={secret}", "digits=6", "algorithm=SHA1", "period=30",
                 "issuer=Portfolio+Tracker"):  # fmt: skip
        assert part in uri


def test_the_qr_code_is_a_square_of_dark_and_light_modules() -> None:
    rows = totp.qr_matrix(totp.provisioning_uri(totp.new_secret(), "anna"))

    assert len(rows) >= 21 and all(len(row) == len(rows) for row in rows)
    assert set("".join(rows)) == {"0", "1"}
    assert rows[0][:7] == "1111111" and rows[1][:7] == "1000001"  # a finder pattern's corner
