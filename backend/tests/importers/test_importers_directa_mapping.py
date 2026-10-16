"""Directa grouping rules beyond the fixture: ambiguities (D-108), quantities (D-106),
foreign currency, pairing, ordering (docs/import/directa.md §2-§3, §8). Invented data only."""

from decimal import Decimal

import pytest
from xlsx_factory import Raw, directa_row, directa_xlsx

from portfolio_tracker.engine.model import TransactionType
from portfolio_tracker.importers.directa_mapping import Category, Entry, Mapped, map_directa
from portfolio_tracker.importers.directa_reader import read_directa

T = TransactionType
ETF = {"ticker": "TETF", "isin": "IE0TEST00020", "description": "TEST ETF"}
BTP = {"ticker": "M.900001", "isin": "IT0TEST00031", "description": "TEST BTP"}
STOCK = {"ticker": "TAZ", "isin": "IT0TEST00015", "description": "TEST AZIONE"}


def mapped(rows: list[list[object]], move_cash: bool = True) -> Mapped:
    return map_directa(read_directa(directa_xlsx(rows)), move_cash=move_cash)


def only(rows: list[list[object]]) -> Entry:
    (entry,) = mapped(rows).entries
    return entry


def codes(entry: Entry) -> list[str]:
    return [r.code for r in entry.reasons]


def buy(amount: object, ref: str = "T1", quantity: object = 10, **kw) -> list[object]:
    return directa_row("Acquisto", amount, quantity=quantity, order_ref=ref, **ETF | kw)


def side(movement: str, amount: object, ref: str = "T1", **kw) -> list[object]:
    return directa_row(movement, amount, order_ref=ref, **BTP | kw)


# --- Orders ----------------------------------------------------------------------------------


def test_side_rows_attach_by_reference_whatever_their_position() -> None:
    """Inverting the file order puts side-rows before the main row (§3)."""
    rows = [
        side("Commissioni", -5),
        side("Ratei pass.obb.", Raw("-1.20")),
        directa_row("Acquisto", -970, quantity=1000, order_ref="T1", **BTP),
        side("Rit.ratei pass.obb.", Raw("0.15")),
    ]
    entry = only(rows)
    assert entry.category is Category.TO_IMPORT
    assert (entry.gross_amount, entry.fees, entry.accrued_interest_paid, entry.taxes) == (
        Decimal(970), Decimal(5), Decimal("1.20"), Decimal("-0.15")
    )  # fmt: skip
    assert entry.total_amount == Decimal("976.05") == 970 + 5 + Decimal("1.20") - Decimal("0.15")
    assert entry.unit_price(is_bond=True) == Decimal(97)


def test_disaggio_alone_makes_the_order_to_review() -> None:
    rows = [directa_row("Acquisto", -970, quantity=1000, order_ref="T1", **BTP),
            side("Rit.credito disaggio", Raw("0.40"))]  # fmt: skip
    entry = only(rows)
    assert entry.category is Category.TO_REVIEW
    assert entry.taxes == Decimal("-0.40")
    (reason,) = entry.reasons
    assert reason.code == "UNCONFIRMED_MOVEMENT_TYPE"
    assert reason.as_dict() == {"original_type": "Rit.credito disaggio", "mapped_as": "TAX_CREDIT"}
    assert entry.selectable and not entry.selected_by_default


def test_buy_without_side_rows_has_zero_fees_and_no_bond_fields() -> None:
    entry = only([buy(-1000)])
    assert (entry.fees, entry.accrued_interest_paid, entry.taxes) == (Decimal(0), None, None)
    assert entry.unit_price(is_bond=False) == Decimal(100)


def test_buy_without_order_reference_is_not_recognized() -> None:
    entry = only([buy(-1000, ref="")])
    assert entry.category is Category.NOT_RECOGNIZED
    assert codes(entry) == ["BUY_WITHOUT_ORDER_REF"]


def test_side_row_without_order_reference_is_not_recognized() -> None:
    entry = only([side("Commissioni", -1, ref="   ")])
    assert entry.category is Category.NOT_RECOGNIZED
    assert codes(entry) == ["SIDE_ROW_WITHOUT_ORDER_REF"]


def test_side_rows_without_a_main_row_are_ambiguous() -> None:
    entry = only([side("Commissioni", -5), side("Ratei pass.obb.", -1)])
    assert entry.category is Category.AMBIGUOUS
    assert codes(entry) == ["ORDER_WITHOUT_MAIN_ROW"]
    assert entry.lines == (11, 12)


@pytest.mark.parametrize("main", ["Vendita", "Tipo sconosciuto"])
def test_order_with_an_unrecognized_row_is_ambiguous_as_a_whole(main: str) -> None:
    rows = [directa_row(main, 500, quantity=5, order_ref="T1", **ETF), side("Commissioni", -2)]
    entry = only(rows)
    assert entry.category is Category.AMBIGUOUS
    assert [(r.code, r.as_dict()) for r in entry.reasons] == [
        ("ORDER_WITH_UNRECOGNIZED_ROW", {"original_type": main})
    ]


def test_order_spread_over_dates_or_isins_is_ambiguous() -> None:
    dates = only([buy(-100, day="10-06-2024"), buy(-100, day="11-06-2024")])
    assert codes(dates) == ["ORDER_MIXED_DATES"]
    isins = only([buy(-100), buy(-100, **STOCK)])
    assert codes(isins) == ["ORDER_MIXED_ISINS"]
    no_isin = only([buy(-100, isin="")])
    assert codes(no_isin) == ["MISSING_ISIN"]


def test_quantity_belongs_to_the_main_row_only() -> None:
    entry = only([buy(-1000), side("Commissioni", -2, quantity=10, **ETF)])
    assert entry.category is Category.AMBIGUOUS
    assert codes(entry) == ["QUANTITY_ON_SIDE_ROW"]
    zero_buy = only([buy(-1000, quantity=0)])
    assert [(r.code, r.as_dict()) for r in zero_buy.reasons] == [
        ("FIELD_RULE", {"field": "quantity"})
    ]


def test_signs_that_break_a_field_rule_are_ambiguous() -> None:
    entry = only([buy(1000)])
    assert [(r.code, r.as_dict()) for r in entry.reasons] == [
        ("FIELD_RULE", {"field": "gross_amount"})
    ]
    fees = only([buy(-1000), side("Commissioni", 2, **ETF)])
    assert [r.as_dict()["field"] for r in fees.reasons] == ["fees"]


def test_cell_problem_makes_the_whole_order_ambiguous() -> None:
    entry = only([buy(-1000), side("Commissioni", "due", **ETF)])
    assert entry.category is Category.AMBIGUOUS
    assert [(r.code, r.as_dict()) for r in entry.reasons] == [
        ("CELL_PROBLEM", {"column": "Importo euro", "problem": "NOT_A_NUMBER", "line": "12"})
    ]


def test_foreign_currency_in_an_order_is_ambiguous() -> None:
    entry = only([buy(-1000, currency="USD", foreign=-1100)])
    assert entry.category is Category.AMBIGUOUS
    assert codes(entry) == ["FOREIGN_CURRENCY"]


def test_partial_executions_merge() -> None:
    entry = only([buy(-150, quantity=30), buy(-101, quantity=20), side("Commissioni", -2.5, **ETF)])
    assert (entry.quantity, entry.gross_amount, entry.fees) == (
        Decimal(50), Decimal(251), Decimal("2.5")
    )  # fmt: skip
    assert [(r.code, r.as_dict()) for r in entry.info] == [("MERGED_EXECUTIONS", {"count": "2"})]


# --- Rows without an order reference --------------------------------------------------------


def test_foreign_currency_row_is_not_recognized() -> None:
    row = directa_row("Conferimento con bonifico", 100, protocol=1, currency="USD")
    entry = only([row])
    assert entry.category is Category.NOT_RECOGNIZED
    assert codes(entry) == ["FOREIGN_CURRENCY"]
    foreign = only([directa_row("Conferimento con bonifico", 100, protocol=1, foreign=90)])
    assert codes(foreign) == ["FOREIGN_CURRENCY"]


def test_duplicate_protocol_is_ambiguous() -> None:
    rows = [
        directa_row("Conferimento con bonifico", 100, protocol=7),
        directa_row("Conferimento con bonifico", 200, protocol=7, day="16-03-2024"),
    ]
    entries = mapped(rows).entries
    assert [e.category for e in entries] == [Category.AMBIGUOUS] * 2
    assert all(codes(e) == ["DUPLICATE_PROTOCOL"] for e in entries)
    assert sorted(e.key for e in entries) == ["line:11", "line:12"]


def test_quantity_on_a_cash_row_is_ambiguous() -> None:
    entry = only([directa_row("Conferimento con bonifico", 100, protocol=1, quantity=3)])
    assert codes(entry) == ["QUANTITY_ON_SIDE_ROW"]


def test_missing_protocol_is_ambiguous() -> None:
    entry = only([directa_row("Tobin tax italia", -1)])
    assert codes(entry) == ["MISSING_PROTOCOL"]


@pytest.mark.parametrize(
    ("movement", "amount"), [("Conferimento con bonifico", -5), ("Bollo portafoglio titoli*", 5)]
)
def test_cash_rows_with_the_wrong_sign_are_ambiguous(movement: str, amount: int) -> None:
    entry = only([directa_row(movement, amount, protocol=1)])
    assert [(r.code, r.as_dict()) for r in entry.reasons] == [
        ("FIELD_RULE", {"field": "total_amount"})
    ]


def test_stamp_duty_asterisk_is_literal() -> None:
    entry = only([directa_row("Bollo portafoglio titoli", -2, protocol=1)])
    assert entry.category is Category.NOT_RECOGNIZED


# --- Income pairing (§3 "Income") ---------------------------------------------------------------


def coupon(amount: object, protocol: int, **kw) -> list[object]:
    return directa_row("Cedola obb.", amount, protocol=protocol, **BTP | kw)


def coupon_tax(amount: object, protocol: int, **kw) -> list[object]:
    return directa_row("Rit.cedola obb.", amount, protocol=protocol, **BTP | kw)


def test_pairing_by_consecutive_protocol() -> None:
    entries = mapped([coupon(50, 10), coupon(30, 12), coupon_tax(-3.75, 13), coupon_tax(-6.25, 11)])
    by_ref = {e.external_ref: e for e in entries.entries}
    assert (by_ref["10"].taxes, by_ref["12"].taxes) == (Decimal("6.25"), Decimal("3.75"))
    assert all([r.code for r in e.info] == ["WITHHOLDING_PAIRED"] for e in by_ref.values())


def test_pairing_inferred_when_exactly_one_candidate() -> None:
    entry = only([coupon(50, 10), coupon_tax(-6.25, 20)])
    assert entry.category is Category.TO_IMPORT
    assert (entry.type, entry.gross_amount, entry.taxes, entry.total_amount) == (
        T.COUPON, Decimal(50), Decimal("6.25"), Decimal("43.75")
    )  # fmt: skip
    assert [r.code for r in entry.info] == ["WITHHOLDING_PAIRED", "PAIRING_INFERRED"]
    assert entry.lines == (11, 12)


def test_pairing_ambiguous_when_several_candidates() -> None:
    rows = [coupon(50, 10), coupon(30, 20), coupon_tax(-6.25, 31), coupon_tax(-3.75, 41)]
    entries = mapped(rows).entries
    assert all(e.category is Category.AMBIGUOUS for e in entries)
    assert all(codes(e) == ["PAIRING_AMBIGUOUS"] for e in entries)


def test_withholding_must_match_kind_date_and_isin() -> None:
    rows = [
        coupon(50, 10),
        directa_row("Ritenuta dividendi italia", -5, protocol=11, **BTP),  # wrong kind
        coupon_tax(-6.25, 21, day="16-03-2024"),  # other day
    ]
    entries = {e.key: e for e in mapped(rows).entries}
    assert entries["protocol:10"].taxes == Decimal(0) and not entries["protocol:10"].info
    assert codes(entries["line:12"]) == ["WITHHOLDING_WITHOUT_INCOME"]
    assert codes(entries["line:13"]) == ["WITHHOLDING_WITHOUT_INCOME"]


def test_income_without_withholding_has_zero_taxes() -> None:
    entry = only([directa_row("Incasso dividendi italia", 20, protocol=5, **STOCK)])
    assert (entry.type, entry.taxes, entry.total_amount) == (T.DIVIDEND, Decimal(0), Decimal(20))


def test_income_without_isin_is_ambiguous() -> None:
    entry = only([directa_row("Incasso dividendi italia", 20, protocol=5)])
    assert codes(entry) == ["MISSING_ISIN"]


def test_positive_withholding_breaks_a_field_rule() -> None:
    entry = only([coupon(50, 10), coupon_tax(6.25, 11)])
    assert [(r.code, r.as_dict()) for r in entry.reasons] == [("FIELD_RULE", {"field": "taxes"})]


# --- Evidence, ordering, option -------------------------------------------------------------------


def test_bond_evidence() -> None:
    rows = [
        buy(-100, ref="T1"),  # ETF: no evidence
        directa_row("Acquisto", -900, quantity=1000, order_ref="T2", isin="IT0TEST00049"),
        directa_row("Cedola obb.", 5, protocol=3, isin="IT0TEST00049"),
        directa_row("Acquisto", -970, quantity=1000, order_ref="T3", **BTP),  # "M." ticker
    ]
    assert mapped(rows).bond_isins == {"IT0TEST00049", "IT0TEST00031"}


def test_sequence_follows_reverse_file_order() -> None:
    """Descending file: the last row is the oldest; a merged order takes its last row (§8)."""
    rows = [
        directa_row("Tobin tax italia", -0.5, protocol=9, day="04-03-2024"),
        buy(-150, ref="T3", day="04-03-2024"),
        buy(-101, ref="T3", day="04-03-2024"),
        directa_row("Conferimento con bonifico", 500, protocol=8, day="01-03-2024"),
    ]
    m = mapped(rows)
    assert [(e.key, e.sequence) for e in m.entries] == [
        ("protocol:8", 1), ("order:T3", 2), ("protocol:9", 3)
    ]  # fmt: skip


def test_rejected_entries_get_no_sequence() -> None:
    m = mapped([directa_row("Sconosciuto", -1, protocol=1), buy(-100)])
    assert [(e.category, e.sequence) for e in m.entries] == [
        (Category.TO_IMPORT, 1), (Category.NOT_RECOGNIZED, None)
    ]  # fmt: skip


def test_option_leaves_rejected_and_review_entries_alone() -> None:
    rows = [
        directa_row("Sconosciuto", -1, protocol=1),
        directa_row("Acquisto", -970, quantity=1000, order_ref="T1", **BTP),
        side("Rit.credito disaggio", 1),
        directa_row("Conferimento con bonifico", 1000, protocol=2),
    ]
    by_key = {e.key: e for e in mapped(rows, move_cash=False).entries}
    assert by_key["line:11"].category is Category.NOT_RECOGNIZED
    assert by_key["order:T1"].category is Category.TO_REVIEW
    assert not by_key["order:T1"].affects_cash
    assert by_key["protocol:2"].category is Category.SKIPPED_BY_CHOICE


def test_date_range() -> None:
    m = mapped([buy(-1, day="20-06-2024"), directa_row("Conferimento con bonifico", 5,
                                                      protocol=1, day="02-01-2024")])  # fmt: skip
    assert (m.first_date.isoformat(), m.last_date.isoformat()) == ("2024-01-02", "2024-06-20")


def test_empty_file() -> None:
    m = mapped([])
    assert (m.entries, m.first_date, m.last_date) == ((), None, None)
