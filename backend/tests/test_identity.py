"""Usernames, names and e-mail addresses at registration (D-173): `identity.py`."""

import pytest

from portfolio_tracker.identity import (
    is_valid_email,
    is_valid_name,
    is_valid_username,
    normalize_email,
    normalize_name,
    normalize_username,
    username_key,
)


@pytest.mark.parametrize(
    "username",
    ["mario", "Mario.Rossi", "ana_maría", "user-01", "a.b@example.org", "x+tag@example.org",
     "Ελένη", "Ñandú", "żółw", "李小龙", "abc", "a" * 64],
)  # fmt: skip
def test_usernames_that_are_accepted(username: str) -> None:
    assert is_valid_username(normalize_username(username))


@pytest.mark.parametrize(
    "username",
    ["ab", "a" * 65, "mario rossi", "mario\trossi", "mario!", "mario#1", "mario/1", "ma'rio",
     "\u0661\u0662\u0663abc", "\u0301abc", "", "   "],
)  # fmt: skip
def test_usernames_that_are_refused(username: str) -> None:
    assert not is_valid_username(normalize_username(username))


def test_a_username_is_kept_as_typed_and_compared_case_folded() -> None:
    decomposed = "A\u0300lex"  # À written as A + combining grave accent
    assert normalize_username(f"  {decomposed} ") == "Àlex"
    assert username_key("Àlex") == username_key("àLEX") == username_key(decomposed) == "àlex"
    assert username_key("STRASSE") == username_key("straße")  # case folding, not lower()
    assert username_key("Mario") != username_key("Maria")


def test_the_length_of_a_username_is_counted_after_nfc() -> None:
    assert is_valid_username(normalize_username("e\u0301" * 64))  # 128 code points, 64 after NFC


@pytest.mark.parametrize(
    ("typed", "stored"),
    [("  Mario  ", "Mario"), ("Anna   Maria", "Anna Maria"), ("D'Angelo", "D'Angelo"),
     ("O\u2019Brien", "O\u2019Brien"), ("Jean-Luc", "Jean-Luc"), ("Zoe\u0308", "Zoë"),
     ("Ελένη", "Ελένη"), ("李", "李")],
)  # fmt: skip
def test_names_that_are_accepted(typed: str, stored: str) -> None:
    assert normalize_name(typed) == stored and is_valid_name(stored)


@pytest.mark.parametrize(
    "name", ["", "   ", "Mario2", "-Mario", "'Mario", "Mario!", "Mario_Rossi", "x" * 101]
)
def test_names_that_are_refused(name: str) -> None:
    assert not is_valid_name(normalize_name(name))


def test_a_name_of_100_letters_is_accepted() -> None:
    assert is_valid_name("x" * 100)


@pytest.mark.parametrize(
    ("typed", "stored"),
    [(" Mario.Rossi@Example.ORG ", "mario.rossi@example.org"),
     ("x+tag@mail.example.org", "x+tag@mail.example.org"),
     ("a_b-c@sub-domain.example.org", "a_b-c@sub-domain.example.org")],
)  # fmt: skip
def test_e_mails_that_are_accepted(typed: str, stored: str) -> None:
    assert normalize_email(typed) == stored and is_valid_email(stored)


@pytest.mark.parametrize(
    "email",
    ["mario", "mario@", "@example.org", "mario@example", "mario@@example.org",
     "ma rio@example.org", "mario@exa mple.org", ".mario@example.org", "mario.@example.org",
     "ma..rio@example.org", "mario@-example.org", "mario@example-.org", "mario@example..org",
     "màrio@example.org", "mario@exämple.org", '"mario"@example.org', "mario@example.org.",
     "a" * 65 + "@example.org", "a@" + "b" * 250 + ".org"],
)  # fmt: skip
def test_e_mails_that_are_refused(email: str) -> None:
    assert not is_valid_email(normalize_email(email))
