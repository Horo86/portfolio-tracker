"""Usernames, names and e-mail addresses at registration (D-173). Pure functions, no I/O.

- **Username:** the login identifier. Letters of any alphabet with their combining marks, the
  digits 0-9 and `. _ - @ +`; no spaces; 3 to 64 characters after Unicode NFC normalisation,
  so an e-mail address can be a username. Stored as typed (NFC); uniqueness, lookup and the
  login throttle use `username_key`, the NFC form case-folded, which is exact beyond ASCII
  (SQLite's NOCASE folds ASCII only).
- **E-mail:** trimmed and lowercased; ASCII, `local@domain` with a dot in the domain, local part
  1 to 64 characters, at most 254 in all, domain labels of letters, digits and hyphens. Not
  verified: nothing is ever sent.
- **First and last name:** NFC, trimmed, inner runs of spaces collapsed to one; 1 to 100
  characters of letters (any alphabet) and their combining marks, spaces, apostrophes (`'` and
  U+2019) and hyphens, starting with a letter.
"""

import re
import unicodedata

USERNAME_MIN, USERNAME_MAX = 3, 64
NAME_MAX = 100
EMAIL_MAX, EMAIL_LOCAL_MAX = 254, 64
USERNAME_SYMBOLS = frozenset("._-@+")
NAME_SYMBOLS = frozenset(" '\u2019-")  # space, apostrophes, hyphen
_DIGITS = frozenset("0123456789")
_SPACES = re.compile(r"\s+")
# The local part: the printable ASCII an address may carry unquoted (RFC 5322 atext and dots).
_LOCAL = re.compile(r"[a-z0-9!#$%&'*+/=?^_`{|}~.-]+")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def _is_letter(char: str) -> bool:
    return unicodedata.category(char).startswith("L")


def _is_mark(char: str) -> bool:
    return unicodedata.category(char).startswith("M")


def normalize_username(text: str) -> str:
    """The username as stored: NFC, without surrounding spaces."""
    return unicodedata.normalize("NFC", text).strip()


def username_key(text: str) -> str:
    """What makes two usernames the same one: the NFC form, case-folded."""
    return normalize_username(text).casefold()


def is_valid_username(username: str) -> bool:
    """`username` as `normalize_username` returns it."""
    if not USERNAME_MIN <= len(username) <= USERNAME_MAX or _is_mark(username[0]):
        return False
    return all(
        _is_letter(char) or _is_mark(char) or char in _DIGITS or char in USERNAME_SYMBOLS
        for char in username
    )


def normalize_name(text: str) -> str:
    return _SPACES.sub(" ", unicodedata.normalize("NFC", text)).strip()


def is_valid_name(name: str) -> bool:
    """`name` as `normalize_name` returns it."""
    if not 1 <= len(name) <= NAME_MAX or not _is_letter(name[0]):
        return False
    return all(_is_letter(char) or _is_mark(char) or char in NAME_SYMBOLS for char in name)


def normalize_email(text: str) -> str:
    return text.strip().lower()


def is_valid_email(email: str) -> bool:
    """`email` as `normalize_email` returns it."""
    if len(email) > EMAIL_MAX or not email.isascii() or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    if not 1 <= len(local) <= EMAIL_LOCAL_MAX or not _LOCAL.fullmatch(local):
        return False
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    labels = domain.split(".")
    return len(labels) >= 2 and all(_LABEL.fullmatch(label) for label in labels)
