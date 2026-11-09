"""Import service with Directa sales (docs/import/directa.md §2a, §3; D-158): the sales fixture
through preview and confirmation, re-import, the order of the files, bonds known to the user,
the notice of a tax to add by hand. Invented data only."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from helpers import details
from sqlalchemy.orm import Session
from xlsx_factory import directa_row, directa_xlsx

from portfolio_tracker.api.errors import ApiError
from portfolio_tracker.engine.ledger import replay
from portfolio_tracker.engine.model import AssetType
from portfolio_tracker.importers.base import Category
from portfolio_tracker.repositories import accounts, assets, mapping, users
from portfolio_tracker.repositories import transactions as txs
from portfolio_tracker.services import imports
from portfolio_tracker.services.imports import AssetChoice, ImportInput

FIXTURES = Path(__file__).parents[3] / "tests/fixtures/directa"
SALES = (FIXTURES / "directa_synthetic_sales_2025.xlsx").read_bytes()
EXPECTED = json.loads((FIXTURES / "directa_synthetic_sales_2025.expected.json").read_text("utf-8"))
TODAY = date(2026, 1, 15)
E1, E2, SH, E3 = "ZZ00TE5TET05", "ZZ0000000024", "ZZ0000000032", "ZZ0000000040"
CHOICES = {f"isin:{i}": AssetChoice(asset_type=AssetType.ETF) for i in (E1, E2, E3)} | {
    f"isin:{SH}": AssetChoice(asset_type=AssetType.STOCK)
}
LINKED = "order:T0000000000105"
ETF = {"ticker": "TETF", "isin": "IE0TEST00020", "description": "TEST ETF"}


@pytest.fixture
def owner(db_session: Session):
    alice = users.add_user(db_session, username="alice", **details("alice"), role="INVESTOR",
                           password_hash="x", language="en")  # fmt: skip
    return alice, accounts.add_account(db_session, alice.id, name="Broker", currency="EUR")


def inp(owner, content: bytes = SALES, **kw) -> ImportInput:
    kw.setdefault("assets", CHOICES)
    return ImportInput(content=content, file_name="directa.xlsx", account_id=owner[1].id, **kw)


def preview(db: Session, owner, data: ImportInput) -> imports.Preview:
    return imports.preview(db, owner[0].id, data, today=TODAY)


def confirm(db: Session, owner, data: ImportInput):
    fingerprint = preview(db, owner, data).fingerprint
    return imports.confirm(db, owner[0].id, owner[0].id, data, fingerprint, today=TODAY,
                           ip="198.51.100.7")  # fmt: skip


def keys(p: imports.Preview, category: Category) -> list[str]:
    return [x.key for x in p.lines if x.candidate.category is category]


def ledger(db: Session, owner):
    rows = txs.account_ledger(db, owner[0].id, owner[1].id)
    return rows, replay([mapping.to_engine_transaction(r) for r in rows])


def test_preview_of_the_sales_fixture(db_session, owner) -> None:
    p = preview(db_session, owner, inp(owner))
    exp = EXPECTED["default_selection"]
    assert sorted(x.candidate.sequence for x in p.lines if x.selected) == exp["to_import_seq"]
    assert keys(p, Category.TO_REVIEW) == [LINKED]
    assert p.blocking == () and p.warnings == () and p.notices == ()


def test_confirm_with_the_linked_sale_then_reimport(db_session, owner) -> None:
    every = frozenset(x.key for x in preview(db_session, owner, inp(owner)).lines)
    done = confirm(db_session, owner, inp(owner, selected=every))
    assert len(done.created) == EXPECTED["transactions_after_merge"]
    rows, book = ledger(db_session, owner)
    assert book.cash == Decimal(EXPECTED["final_cash"])
    sale = next(r for r in rows if r.external_ref == "T0000000000105")
    assert (sale.type, sale.taxes, sale.meta) == (
        "SELL", Decimal("12.87"), {"linked_tax_protocol": "90000117"})  # fmt: skip
    found = assets.assets_by_id(db_session, set(book.positions))
    realized = {found[a].isin: p.realized_net for a, p in book.positions.items()}
    assert realized == {i: Decimal(v["realized_net"]) for i, v in EXPECTED["realized"].items()}

    again = preview(db_session, owner, inp(owner, assets={}))
    assert again.selected_count == 0
    assert again.count(Category.DUPLICATE) == EXPECTED["transactions_after_merge"]
    assert LINKED in keys(again, Category.DUPLICATE)  # the tax row is part of its sale


def test_default_confirm_leaves_the_linked_sale_out(db_session, owner) -> None:
    done = confirm(db_session, owner, inp(owner))
    exp = EXPECTED["default_selection"]
    assert len(done.created) == exp["transactions_imported"]
    assert done.batches[0].to_review_skipped == 1
    _, book = ledger(db_session, owner)
    assert book.cash == Decimal(exp["final_cash"])
    again = preview(db_session, owner, inp(owner, assets={}))
    assert keys(again, Category.TO_REVIEW) == [LINKED] and again.selected_count == 0


# --- The order of the files (D-158 rule 6) -------------------------------------------------------

BOUGHT = directa_xlsx(
    [directa_row("Acquisto", -500, quantity=10, order_ref="B1", day="15-01-2024",
                 value_day="17-01-2024", **ETF)]
)  # fmt: skip
SOLD = directa_xlsx(
    [directa_row("Vendita", 600, quantity=10, order_ref="S1", day="10-09-2025",
                 value_day="12-09-2025", **ETF),
     directa_row("Rit. etf", -26, order_ref="S1", day="10-09-2025", value_day="12-09-2025",
                 **ETF)]
)  # fmt: skip
ETF_CHOICE = {"isin:IE0TEST00020": AssetChoice(asset_type=AssetType.ETF)}


def test_sales_before_their_purchases_are_a_quantity_conflict(db_session, owner) -> None:
    p = preview(db_session, owner, inp(owner, SOLD, assets=ETF_CHOICE))
    assert [(b.code, b.as_dict()["reason"], b.as_dict()["line"]) for b in p.blocking] == [
        ("QUANTITY_CONFLICT", "NEGATIVE_QUANTITY", "order:S1")
    ]
    with pytest.raises(ApiError) as exc:
        confirm(db_session, owner, inp(owner, SOLD, assets=ETF_CHOICE))
    assert (exc.value.status, exc.value.code) == (409, "QUANTITY_CONFLICT")
    assert txs.account_ledger(db_session, owner[0].id, owner[1].id) == []


def test_purchases_first_then_sales_import(db_session, owner) -> None:
    confirm(db_session, owner, inp(owner, BOUGHT, assets=ETF_CHOICE))
    p = preview(db_session, owner, inp(owner, SOLD, assets={}))
    assert p.blocking == () and p.selected_count == 1
    confirm(db_session, owner, inp(owner, SOLD, assets={}))
    _, book = ledger(db_session, owner)
    (position,) = book.positions.values()
    assert (position.quantity, position.realized_gross, position.realized_taxes) == (
        0, Decimal("100.00"), Decimal(26))  # fmt: skip
    assert book.cash == Decimal("74.00")


# --- Bonds and taxes to add by hand -------------------------------------------------------------


def test_a_sale_of_the_users_bond_is_ambiguous(db_session, owner) -> None:
    """The file has no bond evidence; the user's asset says it is a bond (D-158 rule 4)."""
    assets.add_private_asset(db_session, owner[0].id, name="Test bond", isin="IE0TEST00020",
                             asset_type="BOND", quote_currency="EUR",
                             quote_unit="PERCENT_OF_NOMINAL")  # fmt: skip
    p = preview(db_session, owner, inp(owner, SOLD, assets={}))
    (line,) = p.lines
    assert line.candidate.category is Category.AMBIGUOUS and not line.selected
    assert [r.code for r in line.candidate.reasons] == ["BOND_SALE_NOT_VERIFIED"]


def test_a_tax_without_its_sale_is_a_notice_with_its_date(db_session, owner) -> None:
    tax = directa_row("Ritenuta su plusvalenza", -12.5, protocol=90000077, day="03-01-2025",
                      value_day="03-01-2025")  # fmt: skip
    p = preview(db_session, owner, inp(owner, directa_xlsx([tax]), assets={}))
    assert keys(p, Category.AMBIGUOUS) == ["line:11"]
    assert [(n.code, n.as_dict()) for n in p.notices] == [
        ("SALE_TAX_TO_ADD_BY_HAND", {"amount": "12.5", "date": "2025-01-03", "line": "11"})
    ]
