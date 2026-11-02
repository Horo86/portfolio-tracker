"""Re-importing Directa files that overlap or were exported again (directa.md §6, D-160): a line
already present with the same figures is skipped; with other figures, or when the stored sale
lacks the capital gains tax the file now links to it, it is ambiguous with a notice: never
skipped silently, never imported twice, never written over what is stored. Invented data only."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session
from xlsx_factory import directa_row, directa_xlsx

from portfolio_tracker.api.errors import ApiError
from portfolio_tracker.engine.model import AssetType
from portfolio_tracker.importers.base import Category
from portfolio_tracker.repositories import accounts, users
from portfolio_tracker.repositories import transactions as txs
from portfolio_tracker.services import imports
from portfolio_tracker.services.imports import AssetChoice, ImportInput

TODAY = date(2026, 1, 15)
ETF = {"ticker": "TETF", "isin": "IE0TEST00020", "description": "TEST ETF"}
CHOICE = {"isin:IE0TEST00020": AssetChoice(asset_type=AssetType.ETF)}
DIFFERENT = ("ALREADY_PRESENT_DIFFERENT", {})
SAME = ("ALREADY_PRESENT", {"match": "external_ref_and_type"})


@pytest.fixture
def owner(db_session: Session):
    alice = users.add_user(db_session, username="alice", role="INVESTOR", password_hash="x",
                           language="en")  # fmt: skip
    return alice, accounts.add_account(db_session, alice.id, name="Broker", currency="EUR")


def buy(amount: float, quantity: int, day: str = "15-01-2024", **kw) -> list[object]:
    return directa_row("Acquisto", amount, quantity=quantity, order_ref="B1", day=day,
                       value_day="17-01-2024", **ETF | kw)  # fmt: skip


def side(movement: str, amount: float, ref: str, day: str, value_day: str) -> list[object]:
    return directa_row(movement, amount, order_ref=ref, day=day, value_day=value_day, **ETF)


def sell(amount: float = 600, quantity: int = 10) -> list[object]:
    return directa_row("Vendita", amount, quantity=quantity, order_ref="S1", day="10-09-2025",
                       value_day="12-09-2025", **ETF)  # fmt: skip


GAIN_TAX = directa_row("Ritenuta su plusvalenza", -26, protocol=90000001, day="12-09-2025",
                       value_day="12-09-2025")  # fmt: skip
BOUGHT = directa_xlsx([buy(-500, 10)])
EARLY = directa_xlsx([sell()])  # exported before the tax was charged
LATE = directa_xlsx([GAIN_TAX, sell()])  # the same sale, exported again with its tax (row 11)
TAX_TO_ADD = ("SALE_TAX_TO_ADD_BY_HAND", {"amount": "26", "date": "2025-09-12", "line": "11"})


def inp(owner, content: bytes, **kw) -> ImportInput:
    kw.setdefault("assets", CHOICE)
    return ImportInput(content=content, file_name="directa.xlsx", account_id=owner[1].id, **kw)


def preview(db: Session, owner, content: bytes, **kw) -> imports.Preview:
    return imports.preview(db, owner[0].id, inp(owner, content, **kw), today=TODAY)


def confirm(db: Session, owner, content: bytes, **kw):
    data = inp(owner, content, **kw)
    fingerprint = imports.preview(db, owner[0].id, data, today=TODAY).fingerprint
    return imports.confirm(db, owner[0].id, owner[0].id, data, fingerprint, today=TODAY)


def confirm_all(db: Session, owner, content: bytes) -> None:
    every = frozenset(x.key for x in preview(db, owner, content).lines if x.candidate.selectable)
    confirm(db, owner, content, selected=every)


def line(p: imports.Preview, key: str) -> imports.Line:
    return next(x for x in p.lines if x.key == key)


def reasons(x: imports.Line) -> list[tuple[str, dict[str, str]]]:
    return [(r.code, r.as_dict()) for r in x.candidate.reasons]


def notices(p: imports.Preview) -> list[tuple[str, dict[str, str]]]:
    return sorted((n.code, n.as_dict()) for n in p.notices)


def stored(db: Session, owner) -> list[tuple]:
    rows = txs.account_ledger(db, owner[0].id, owner[1].id)
    return [(r.external_ref, r.type, r.quantity, r.gross_amount, r.fees, r.taxes, r.trade_date,
             r.meta) for r in rows]  # fmt: skip


def assert_ambiguous_and_untouched(db: Session, owner, content: bytes, key: str) -> imports.Line:
    """The line cannot be selected, and the stored data stays exactly as it was."""
    before = stored(db, owner)
    p = preview(db, owner, content, assets={})
    found = line(p, key)
    assert found.candidate.category is Category.AMBIGUOUS and not found.selected
    assert reasons(found) == [DIFFERENT]
    with pytest.raises(ApiError) as exc:
        confirm(db, owner, content, assets={}, selected=frozenset({key}))
    assert (exc.value.status, exc.value.code) == (422, "IMPORT_SELECTION_INVALID")
    assert stored(db, owner) == before
    return found


# --- The same figures ------------------------------------------------------------------------


def test_an_unchanged_reimport_is_already_present(db_session, owner) -> None:
    confirm(db_session, owner, BOUGHT)
    p = preview(db_session, owner, BOUGHT, assets={})
    assert reasons(line(p, "order:B1")) == [SAME]
    assert line(p, "order:B1").candidate.category is Category.DUPLICATE
    assert p.notices == ()


def test_an_unchanged_reimport_of_a_linked_sale_is_already_present(db_session, owner) -> None:
    confirm(db_session, owner, BOUGHT)
    confirm_all(db_session, owner, LATE)
    p = preview(db_session, owner, LATE, assets={})
    assert line(p, "order:S1").candidate.category is Category.DUPLICATE
    assert p.notices == ()


# --- Other figures (D-160) -------------------------------------------------------------------


@pytest.mark.parametrize(
    "again",
    [
        pytest.param([side("Commissioni", -2, "B1", "15-01-2024", "17-01-2024"), buy(-500, 10)],
                     id="fees"),
        pytest.param([buy(-500, 11)], id="quantity"),
        pytest.param([buy(-501, 10)], id="gross"),
        pytest.param([buy(-500, 10, day="16-01-2024")], id="date"),
    ],
)  # fmt: skip
def test_other_figures_make_the_line_ambiguous_with_a_notice(db_session, owner, again) -> None:
    confirm(db_session, owner, BOUGHT)
    assert_ambiguous_and_untouched(db_session, owner, directa_xlsx(again), "order:B1")
    p = preview(db_session, owner, directa_xlsx(again), assets={})
    assert notices(p) == [("ALREADY_PRESENT_DIFFERENT", {"count": "1"})]


def test_a_deposit_with_another_amount_is_ambiguous(db_session, owner) -> None:
    def deposit(amount: int) -> bytes:
        return directa_xlsx([directa_row("Conferimento con bonifico", amount, protocol=90000009)])

    confirm(db_session, owner, deposit(1000))
    assert_ambiguous_and_untouched(db_session, owner, deposit(1200), "protocol:90000009")


# --- An order cut off by the 3000-row limit, in both file orders -----------------------------

PARTIAL = directa_xlsx([buy(-300, 6)])  # the last file stopped in the middle of the order
COMPLETE = directa_xlsx([buy(-200, 4), buy(-300, 6)])  # the next file has every execution


def test_a_cut_off_order_then_the_complete_one(db_session, owner) -> None:
    confirm(db_session, owner, PARTIAL)
    assert_ambiguous_and_untouched(db_session, owner, COMPLETE, "order:B1")
    (row,) = stored(db_session, owner)
    assert row[2] == 6  # the partial quantity is still there, for the user to correct


def test_the_complete_order_then_a_cut_off_one(db_session, owner) -> None:
    confirm(db_session, owner, COMPLETE)
    assert_ambiguous_and_untouched(db_session, owner, PARTIAL, "order:B1")


# --- A capital gains tax charged after the first export, in both file orders -----------------


def test_a_sale_imported_without_its_tax_then_the_file_with_the_tax(db_session, owner) -> None:
    confirm(db_session, owner, BOUGHT)
    confirm(db_session, owner, EARLY)
    assert_ambiguous_and_untouched(db_session, owner, LATE, "order:S1")
    p = preview(db_session, owner, LATE, assets={})
    assert notices(p) == [("ALREADY_PRESENT_DIFFERENT", {"count": "1"}), TAX_TO_ADD]


def test_a_sale_imported_with_its_tax_then_a_file_without_it(db_session, owner) -> None:
    confirm(db_session, owner, BOUGHT)
    confirm_all(db_session, owner, LATE)
    assert_ambiguous_and_untouched(db_session, owner, EARLY, "order:S1")
    p = preview(db_session, owner, EARLY, assets={})
    assert notices(p) == [("ALREADY_PRESENT_DIFFERENT", {"count": "1"})]


def test_a_tax_added_by_hand_still_lacks_the_linked_protocol(db_session, owner) -> None:
    """The stored sale has the right figures but not the protocol of the tax row the file links
    to it: the line stays ambiguous, with the notice (D-160)."""
    confirm(db_session, owner, BOUGHT)
    confirm(db_session, owner, EARLY)
    sale = next(r for r in txs.account_ledger(db_session, owner[0].id, owner[1].id)
                if r.external_ref == "S1")  # fmt: skip
    sale.taxes, sale.total_amount = Decimal(26), Decimal(574)
    db_session.flush()
    assert_ambiguous_and_untouched(db_session, owner, LATE, "order:S1")
    assert TAX_TO_ADD in notices(preview(db_session, owner, LATE, assets={}))
