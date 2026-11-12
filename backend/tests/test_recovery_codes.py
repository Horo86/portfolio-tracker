"""Recovery codes (D-174): ten codes of 16 readable characters, only their SHA-256 kept."""

import hashlib

import pytest

from portfolio_tracker.auth import recovery
from portfolio_tracker.auth.passwords import READABLE_ALPHABET


def test_ten_different_codes_of_16_readable_characters_in_four_groups() -> None:
    codes = recovery.new_codes()

    assert len(codes) == len(set(codes)) == 10
    for code in codes:
        groups = code.split("-")
        assert [len(group) for group in groups] == [4, 4, 4, 4]
        assert set("".join(groups)) <= set(READABLE_ALPHABET)
    assert not set("0O1lI") & set(READABLE_ALPHABET)


def test_the_stored_form_is_the_sha_256_of_the_16_characters() -> None:
    assert recovery.hash_code("abcd-efgh-jkmn-pqrs") == recovery.hash_code("abcdefghjkmnpqrs")
    assert recovery.hash_code("abcd-efgh-jkmn-pqrs") == (
        hashlib.sha256(b"abcdefghjkmnpqrs").hexdigest()
    )


@pytest.mark.parametrize(
    "typed", ["abcd-efgh-jkmn-pqrs", " abcdefghjkmnpqrs ", "abcd efgh jkmn pqrs"]
)
def test_hyphens_and_spaces_are_ignored_when_a_code_is_typed(typed: str) -> None:
    hashes = [recovery.hash_code(code) for code in ("zzzz-zzzz-zzzz-zzzz", "abcd-efgh-jkmn-pqrs")]

    assert recovery.find(typed, hashes) == 1


@pytest.mark.parametrize(
    "typed", ["", "abcd-efgh-jkmn-pqr", "abcd-efgh-jkmn-pqrst", "ABCD-EFGH-JKMN-PQRS",
              "abcd-efgh-jkmn-pqr0", "123456", "----------------"],
)  # fmt: skip
def test_a_wrong_or_misshapen_code_is_not_found(typed: str) -> None:
    hashes = [recovery.hash_code("abcd-efgh-jkmn-pqrs")]

    assert recovery.find(typed, hashes) is None


def test_no_hash_matches_what_stands_in_for_a_misshapen_code() -> None:
    """A code that has not a code's shape is compared as a filler: even a stored hash of that
    filler is not found."""
    filler = hashlib.sha256(("-" * 16).encode()).hexdigest()

    assert recovery.find("nonsense", [filler]) is None


def test_every_stored_hash_is_compared_in_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    compared: list[str] = []
    real = recovery.hmac.compare_digest

    def spy(left: str, right: str) -> bool:
        compared.append(left)
        return real(left, right)

    monkeypatch.setattr(recovery.hmac, "compare_digest", spy)
    codes = recovery.new_codes()
    hashes = [recovery.hash_code(code) for code in codes]

    assert recovery.find(codes[0], hashes) == 0
    assert compared == hashes  # all ten, also after the first one matched
    compared.clear()
    assert recovery.find("nonsense", hashes) is None
    assert compared == hashes
