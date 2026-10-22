"""Every code an import can send to the screens has a text in both languages of the web UI
(D-142): the codes are the members of `importers.codes` (plus the preview categories and
the engine's quantity conflicts), the texts are in the frontend's translation files. A new
code without its texts fails here, not in front of a user."""

import json
from enum import StrEnum
from pathlib import Path

import pytest

from portfolio_tracker.engine.ledger import ConflictCode
from portfolio_tracker.importers.base import Category
from portfolio_tracker.importers.codes import (
    BlockingCode,
    CellProblem,
    FieldProblem,
    ImportErrorCode,
    LineCode,
    NoticeCode,
)

LOCALES = Path(__file__).parents[2] / "frontend/src/locales"

# Section of the translation files -> the codes it must translate.
SECTIONS: dict[str, type[StrEnum]] = {
    "errors": ImportErrorCode,
    "importCategories": Category,
    "importLines": LineCode,
    "importNotices": NoticeCode,
    "importBlocking": BlockingCode,
    "importConflicts": ConflictCode,
    "importCellProblems": CellProblem,
    "importFieldProblems": FieldProblem,
}


@pytest.mark.parametrize("language", ["it", "en"])
def test_every_import_code_has_a_text(language: str) -> None:
    texts = json.loads((LOCALES / f"{language}.json").read_text("utf-8"))
    missing = [
        f"{section}.{code.value}"
        for section, codes in SECTIONS.items()
        for code in codes
        if not texts.get(section, {}).get(code.value, "").strip()
    ]
    assert missing == []
