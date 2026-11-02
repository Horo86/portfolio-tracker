"""Directa sales (docs/import/directa.md §2a, §3; D-158): the sales fixture against its expected
outcome, and the rules built in code. Invented data only."""

import json
import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from xlsx_factory import directa_row, directa_xlsx

from portfolio_tracker.engine import model as engine
from portfolio_tracker.engine.ledger import replay
from portfolio_tracker.engine.ordering import replay_order
from portfolio_tracker.engine.validation import validate_transaction
from portfolio_tracker.importers.base import Candidate as Entry
from portfolio_tracker.importers.base import Category, derived_unit_price
from portfolio_tracker.importers.base import ParsedImport as Mapped
from portfolio_tracker.importers.directa_mapping import map_directa
from portfolio_tracker.importers.directa_reader import read_directa

FIXTURES = Path(__file__).parents[3] / "tests/fixtures/directa"
FILE = FIXTURES / "directa_synthetic_sales_2025.xlsx"
EXPECTED = json.loads((FIXTURES / "directa_synthetic_sales_2025.expected.json").read_text("utf-8"))
T = engine.TransactionType
E1, E2, SH, E3 = "ZZ00TE5TET05", "ZZ0000000024", "ZZ0000000032", "ZZ0000000040"
TYPES = {E1: engine.AssetType.ETF, E2: engine.AssetType.ETF, SH: engine.AssetType.STOCK,
         E3: engine.AssetType.ETF}  # fmt: skip
ASSETS = {
    isin: engine.Asset(id=uuid.uuid5(uuid.NAMESPACE_URL, isin), asset_type=kind,
                       quote_currency="EUR", quote_unit=engine.QuoteUnit.CURRENCY)
    for isin, kind in TYPES.items()
}  # fmt: skip
ISIN_OF = {a.id: isin for isin, a in ASSETS.items()}
SALE_105 = "order:T0000000000105"


def fixture(move_cash: bool = True) -> Mapped:
    return map_directa(read_directa(FILE.read_bytes()), move_cash=move_cash)


def to_engine(entry: Entry) -> engine.Transaction:
    asset = ASSETS[entry.asset.isin] if entry.asset else None
    tx = engine.Transaction(
        id=uuid.uuid5(uuid.NAMESPACE_URL, entry.key),
        type=entry.type,
        trade_date=entry.trade_date,
        value_date=entry.value_date,
        sequence=entry.sequence,
        asset_id=asset.id if asset else None,
        quantity=entry.quantity,
        unit_price=derived_unit_price(entry, is_bond=False),
        gross_amount=entry.gross_amount,
        fees=entry.fees,
        accrued_interest_paid=entry.accrued_interest_paid,
        accrued_interest_received=entry.accrued_interest_received,
        taxes=entry.taxes,
        total_amount=entry.total_amount,
        affects_cash=entry.affects_cash,
    )
    assert validate_transaction(tx, asset) == [], entry.key
    return tx


def dec(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def cash_effect(tx: engine.Transaction) -> Decimal:
    if not tx.affects_cash:
        return Decimal(0)
    return -tx.total_amount if tx.type in (T.BUY, T.WITHDRAWAL) else tx.total_amount


def open_positions(ledger) -> dict[str, tuple[Decimal, Decimal]]:
    return {
        ISIN_OF[a]: (p.quantity, p.cost_basis) for a, p in ledger.positions.items() if p.quantity
    }


def wanted_positions(block: dict) -> dict[str, tuple[Decimal, Decimal]]:
    return {i: (Decimal(v["quantity"]), Decimal(v["cost_basis"])) for i, v in block.items()}


# --- The fixture --------------------------------------------------------------------------------


def test_fixture_counts() -> None:
    m = fixture()
    assert m.account_code == EXPECTED["account_code"]
    assert sum(len(e.lines) for e in m.candidates) == EXPECTED["rows_in_file"]
    assert sum(e.selectable for e in m.candidates) == EXPECTED["transactions_after_merge"]
    assert Counter(c.category.value for c in m.candidates) == EXPECTED["categories"]
    assert m.notices == ()


def test_fixture_default_selection_leaves_the_linked_sale_to_review() -> None:
    m, exp = fixture(), EXPECTED["default_selection"]
    default = [e for e in m.candidates if e.selected_by_default]
    assert sorted(e.sequence for e in default) == exp["to_import_seq"]
    review = [e for e in m.candidates if e.category is Category.TO_REVIEW]
    assert [
        {"seq": e.sequence, "external_ref": e.external_ref,
         "reasons": [{"code": r.code, **r.as_dict()} for r in e.reasons]}
        for e in review
    ] == exp["to_review"]  # fmt: skip
    ledger = replay([to_engine(e) for e in default])
    assert len(default) == exp["transactions_imported"]
    assert ledger.cash == Decimal(exp["final_cash"])
    assert open_positions(ledger) == wanted_positions(exp["open_positions"])
    assert ledger.warnings == ()


def test_fixture_all_transactions_after_selecting_the_sale_to_review() -> None:
    m = fixture()
    entries = {e.sequence: e for e in m.candidates if e.selectable}
    txs = [to_engine(e) for e in entries.values()]
    cash = Decimal(0)
    pairs = zip(replay_order(txs), EXPECTED["transactions"], strict=True)
    for order, (tx, exp) in enumerate(pairs):
        entry = entries[tx.sequence]
        cash += cash_effect(tx)
        assert exp["replay_order"] == order + 1
        actual = {
            "seq": tx.sequence, "type": tx.type.value, "trade_date": tx.trade_date.isoformat(),
            "value_date": tx.value_date.isoformat(),
            "isin": entry.asset.isin if entry.asset else None, "external_ref": entry.external_ref,
            "metadata": dict(entry.metadata) if entry.metadata else None,
            "affects_cash": tx.affects_cash, "source_rows": len(entry.lines),
        }  # fmt: skip
        figures = ("quantity", "unit_price", "gross_amount", "fees", "taxes", "total_amount")
        assert actual == {k: exp[k] for k in actual}, exp["seq"]
        assert {k: getattr(tx, k) for k in figures} == {k: dec(exp[k]) for k in figures}
        assert cash == Decimal(exp["cash_after"])
        assert tx.accrued_interest_received is None and tx.accrued_interest_paid is None

    ledger = replay(txs)
    assert ledger.cash == Decimal(EXPECTED["final_cash"])
    assert ledger.warnings == ()
    assert open_positions(ledger) == {}
    realized = {
        ISIN_OF[a]: {
            "cost_removed": p.cost_removed, "realized_gross": p.realized_gross,
            "realized_taxes": p.realized_taxes, "realized_net": p.realized_net,
        }
        for a, p in ledger.positions.items()
    }  # fmt: skip
    assert realized == {
        isin: {k: Decimal(v) for k, v in block.items()}
        for isin, block in EXPECTED["realized"].items()
    }


def test_fixture_partial_fill_is_one_sale() -> None:
    exp = EXPECTED["partial_fill"]
    (entry,) = [e for e in fixture().candidates if e.external_ref == exp["external_ref"]]
    assert entry.type is T.SELL and entry.category is Category.TO_IMPORT
    assert entry.quantity == Decimal(exp["quantity"])
    assert derived_unit_price(entry, is_bond=False) == Decimal(exp["unit_price"])
    assert (entry.gross_amount, entry.fees, entry.taxes, entry.total_amount) == (
        Decimal(exp["gross_amount"]), Decimal(exp["fees"]), Decimal(exp["taxes"]),
        Decimal(exp["cash_effect"]))  # fmt: skip
    assert [{"code": r.code, **r.as_dict()} for r in entry.info] == exp["info"]


def test_fixture_linked_tax_belongs_to_its_sale() -> None:
    exp = EXPECTED["linked_tax"]
    entry = next(e for e in fixture().candidates if e.key == SALE_105)
    assert exp["tax_row"] in entry.lines
    assert entry.external_ref == exp["sale_external_ref"]
    assert entry.metadata == {"linked_tax_protocol": exp["tax_protocol"]}
    assert entry.taxes == Decimal(exp["taxes"])
    assert entry.selectable and not entry.selected_by_default


def test_fixture_do_not_move_cash() -> None:
    m, exp = fixture(move_cash=False), EXPECTED["do_not_move_cash_option"]
    assert (
        sorted(e.sequence for e in m.candidates if e.selected_by_default)
        == (exp["default_selected_seq"])
    )
    skipped = [e.sequence for e in m.candidates if e.category is Category.SKIPPED_BY_CHOICE]
    assert skipped == exp["skipped_by_default_seq"]
    assert [e.sequence for e in m.candidates if e.category is Category.TO_REVIEW] == (
        exp["to_review_seq"]
    )
    chosen = [e for e in m.candidates if e.selected_by_default]
    assert all(not e.affects_cash for e in chosen)
    assert replay([to_engine(e) for e in chosen]).cash == Decimal(exp["final_cash"])


# --- Rules built in code ---------------------------------------------------------------------

ETF = {"ticker": "TETF", "isin": "IE0TEST00020", "description": "TEST ETF"}
STOCK = {"ticker": "TAZ", "isin": "IT0TEST00015", "description": "TEST AZIONE"}
BTP = {"ticker": "M.900001", "isin": "IT0TEST00031", "description": "TEST BTP"}
DAY, SETTLE = "14-04-2025", "16-04-2025"
CREDIT, IN_SALE = "TAX_CREDIT_NOT_VERIFIED", "ROW_NOT_VERIFIED_IN_SALE"
GAIN = "Ritenuta su plusvalenza"


def mapped(rows: list[list[object]]) -> Mapped:
    return map_directa(read_directa(directa_xlsx(rows)))


def only(rows: list[list[object]]) -> Entry:
    (entry,) = mapped(rows).candidates
    return entry


def codes(entry: Entry) -> list[str]:
    return [r.code for r in entry.reasons]


def row(movement: str, amount: object, ref: str = "T1", asset: dict = ETF, **kw) -> list[object]:
    kw = {"day": DAY, "value_day": SETTLE, "order_ref": ref} | asset | kw
    return directa_row(movement, amount, **kw)


def sale(amount: object = 500, quantity: object = 5, ref: str = "T1", **kw) -> list[object]:
    return row("Vendita", amount, ref, quantity=quantity, **kw)


def gain_tax(amount: object = -10, protocol: object = 90000050, **kw) -> list[object]:
    kw = {"day": SETTLE, "value_day": SETTLE} | kw
    return directa_row("Ritenuta su plusvalenza", amount, protocol=protocol, **kw)


def by_key(m: Mapped) -> dict[str, Entry]:
    return {e.key: e for e in m.candidates}


def test_a_sale_with_fees_and_withholding_is_one_sell() -> None:
    entry = only([sale(500.4), row("Commissioni", -2.5), row("Rit. etf", -13.1)])
    assert (entry.category, entry.type, entry.key, entry.external_ref) == (
        Category.TO_IMPORT, T.SELL, "order:T1", "T1")  # fmt: skip
    figures = (entry.quantity, entry.gross_amount, entry.fees, entry.taxes, entry.total_amount)
    assert figures == tuple(map(Decimal, ("5", "500.4", "2.5", "13.1", "484.8")))
    assert entry.accrued_interest_received is None and entry.metadata is None
    assert (entry.trade_date.isoformat(), entry.value_date.isoformat()) == (
        "2025-04-14", "2025-04-16")  # fmt: skip
    assert entry.asset.isin == ETF["isin"] and entry.asset.asset_type is None


def test_a_sale_without_side_rows_has_no_fees_and_no_taxes() -> None:
    entry = only([sale(500)])
    assert (entry.fees, entry.taxes, entry.total_amount) == (0, 0, Decimal(500))


def test_partial_executions_of_a_sale_are_merged() -> None:
    """D-158 rule 1: derived from purchases, not yet confirmed by a real sale in parts."""
    rows = [sale(240, 2), row("Rit. etf", -4), sale(361.5, 3), row("Commissioni", -1.5)]
    entry = only(rows)
    assert entry.category is Category.TO_IMPORT and entry.type is T.SELL
    assert (entry.quantity, entry.gross_amount, entry.fees, entry.taxes) == (
        Decimal(5), Decimal("601.5"), Decimal("1.5"), Decimal(4))  # fmt: skip
    assert entry.total_amount == Decimal("596.0") == sum(Decimal(str(r[8])) for r in rows)
    assert derived_unit_price(entry, is_bond=False) == Decimal("120.3")  # weighted
    assert [(r.code, r.as_dict()) for r in entry.info] == [("MERGED_EXECUTIONS", {"count": "2"})]


@pytest.mark.parametrize(
    ("parts", "reason"),
    [
        ([sale(240, 2), sale(-10, 3)], ("FIELD_RULE", {"field": "gross_amount", "line": "12"})),
        ([sale(240, 2), sale(0, 3)], ("FIELD_RULE", {"field": "gross_amount", "line": "12"})),
        ([sale(240, 2), sale(10, 0)], ("FIELD_RULE", {"field": "quantity", "line": "12"})),
    ],
)
def test_a_part_breaking_the_rules_makes_the_whole_sale_ambiguous(parts, reason) -> None:
    entry = only(parts)
    assert entry.category is Category.AMBIGUOUS and entry.lines == (11, 12)
    assert [(r.code, r.as_dict()) for r in entry.reasons] == [reason]


@pytest.mark.parametrize(
    ("side", "reason"),
    [
        (row("Commissioni", 2), ("FIELD_RULE", {"field": "fees", "line": "12"})),
        (row("Commissioni", 0), ("FIELD_RULE", {"field": "fees", "line": "12"})),
        (row("Rit. etf", 3), (CREDIT, {"original_type": "Rit. etf", "line": "12"})),
        (row("Rit. etf", 0), ("FIELD_RULE", {"field": "taxes", "line": "12"})),
        (row("Ratei pass.obb.", -1), (IN_SALE, {"original_type": "Ratei pass.obb."})),
        (row("Rit.ratei pass.obb.", 1), (IN_SALE, {"original_type": "Rit.ratei pass.obb."})),
    ],
)  # fmt: skip
def test_sale_side_rows_must_be_verified_and_negative(side, reason) -> None:
    entry = only([sale(), side])
    assert entry.category is Category.AMBIGUOUS
    assert [(r.code, r.as_dict()) for r in entry.reasons] == [reason]


def test_withholding_of_a_sale_in_a_purchase_is_ambiguous() -> None:
    entry = only([row("Acquisto", -500, quantity=5), row("Rit. etf", -1)])
    assert [(r.code, r.as_dict()) for r in entry.reasons] == [
        ("ROW_NOT_VERIFIED_IN_PURCHASE", {"original_type": "Rit. etf"})
    ]


def test_an_order_mixing_purchases_and_sales_is_ambiguous() -> None:
    entry = only([row("Acquisto", -500, quantity=5), sale()])
    assert entry.category is Category.AMBIGUOUS and codes(entry) == ["ORDER_MIXED_SIDES"]


def test_a_sale_withholding_alone_has_no_main_row() -> None:
    entry = only([row("Rit. etf", -1)])
    assert codes(entry) == ["ORDER_WITHOUT_MAIN_ROW"]


def test_a_bond_sale_is_ambiguous() -> None:
    """D-158 rule 4: the rows of a bond sale (accrued interest) were never seen."""
    entry = only([sale(4950, 5000, asset=BTP)])
    assert entry.category is Category.AMBIGUOUS and codes(entry) == ["BOND_SALE_NOT_VERIFIED"]
    coupon = directa_row("Cedola obb.", 10, protocol=90000001, **ETF)
    entries = by_key(mapped([sale(), coupon]))
    assert codes(entries["order:T1"]) == ["BOND_SALE_NOT_VERIFIED"]  # proven a bond by the coupon


def test_a_sale_of_another_currency_is_not_recognized_as_a_sale() -> None:
    entry = only([sale(currency="USD")])
    assert entry.category is Category.AMBIGUOUS and codes(entry) == ["FOREIGN_CURRENCY"]


# --- "Ritenuta su plusvalenza" (D-158 rules 2 and 3) -------------------------------------------


def test_the_tax_links_to_the_only_sale_settling_on_its_date() -> None:
    m = mapped([gain_tax(-12.87), sale(351.4, 20, asset=STOCK)])
    (entry,) = m.candidates
    assert entry.category is Category.TO_REVIEW and entry.type is T.SELL
    assert entry.lines == (11, 12)
    assert (entry.taxes, entry.total_amount) == (Decimal("12.87"), Decimal("338.53"))
    assert entry.metadata == {"linked_tax_protocol": "90000050"}
    assert [(r.code, r.as_dict()) for r in entry.reasons] == [
        ("SALE_TAX_LINKED_BY_DATE", {"amount": "12.87", "date": "2025-04-16", "line": "11"})
    ]
    assert m.notices == ()
    assert entry.sequence == 1


def test_the_tax_links_to_a_sale_order_in_several_parts() -> None:
    """One merged order, not one row: two executions of the same sale are one match."""
    m = mapped([gain_tax(), sale(100, 1), sale(200, 2)])
    (entry,) = m.candidates
    assert entry.category is Category.TO_REVIEW and entry.lines == (11, 12, 13)
    assert entry.taxes == Decimal(10) and entry.quantity == Decimal(3)


def test_the_tax_and_its_sale_keep_the_sales_sequence() -> None:
    rows = [gain_tax(), directa_row("Conferimento con bonifico", 100, day="15-04-2025",
            value_day="15-04-2025", protocol=90000040), sale()]  # fmt: skip
    entries = by_key(mapped(rows))
    assert entries["order:T1"].sequence == 1 and entries["protocol:90000040"].sequence == 2


def notice(m: Mapped) -> list[tuple[str, dict[str, str]]]:
    return [(n.code, n.as_dict()) for n in m.notices]


TO_ADD = ("SALE_TAX_TO_ADD_BY_HAND", {"amount": "10", "date": "2025-04-16", "line": "11"})


def test_a_tax_without_a_sale_on_its_date_is_ambiguous_with_a_notice() -> None:
    m = mapped([gain_tax(), sale(value_day="17-04-2025")])
    entries = by_key(m)
    assert entries["line:11"].category is Category.AMBIGUOUS
    assert codes(entries["line:11"]) == ["SALE_TAX_NO_SALE"]
    assert entries["order:T1"].category is Category.TO_IMPORT
    assert entries["order:T1"].taxes == 0
    assert notice(m) == [TO_ADD]


def test_a_sale_with_its_own_withholding_is_not_a_match() -> None:
    m = mapped([gain_tax(), sale(), row("Rit. etf", -3)])
    assert codes(by_key(m)["line:11"]) == ["SALE_TAX_NO_SALE"]
    assert by_key(m)["order:T1"].taxes == 3
    assert notice(m) == [TO_ADD]


def test_a_tax_matching_two_sales_is_ambiguous_with_a_notice() -> None:
    m = mapped([gain_tax(), sale(ref="T1"), sale(ref="T2", asset=STOCK)])
    entries = by_key(m)
    assert [(r.code, r.as_dict()) for r in entries["line:11"].reasons] == [
        ("SALE_TAX_SEVERAL_SALES", {"count": "2"})
    ]
    assert entries["order:T1"].category is entries["order:T2"].category is Category.TO_IMPORT
    assert notice(m) == [TO_ADD]


def test_a_tax_whose_only_sale_is_ambiguous_is_ambiguous_too() -> None:
    m = mapped([gain_tax(), sale(), row("Commissioni", 1)])
    entries = by_key(m)
    assert entries["order:T1"].category is Category.AMBIGUOUS
    assert [(r.code, r.as_dict()) for r in entries["line:11"].reasons] == [
        ("SALE_TAX_SALE_REJECTED", {"external_ref": "T1"})
    ]
    assert notice(m) == [TO_ADD]


def test_two_taxes_for_one_sale_are_both_ambiguous() -> None:
    m = mapped([gain_tax(protocol=90000050), gain_tax(-5, protocol=90000052), sale()])
    entries = by_key(m)
    assert codes(entries["line:11"]) == codes(entries["line:12"]) == ["SALE_TAX_SHARED"]
    assert entries["order:T1"].category is Category.TO_IMPORT and entries["order:T1"].taxes == 0
    assert [n.as_dict()["line"] for n in m.notices] == ["11", "12"]


def test_a_tax_dated_otherwise_than_settled_does_not_link() -> None:
    """Its operation date and value date must both be the sale's value date."""
    m = mapped([gain_tax(day="15-04-2025"), sale()])
    assert codes(by_key(m)["line:11"]) == ["SALE_TAX_NO_SALE"]


LOST = ("SALE_TAX_ROW_REJECTED", {"line": "11"})


@pytest.mark.parametrize(
    ("tax", "reason", "amount"),
    [
        (gain_tax(10), (CREDIT, {"original_type": GAIN, "line": "11"}), "-10"),
        (gain_tax(0), ("FIELD_RULE", {"field": "taxes", "line": "11"}), "0"),
        (gain_tax(protocol=""), ("MISSING_PROTOCOL", {}), "10"),
        (gain_tax(quantity=1), ("QUANTITY_ON_SIDE_ROW", {"original_type": GAIN}), "10"),
        (gain_tax(currency="USD"), ("FOREIGN_CURRENCY", {}), "10"),
    ],
)  # fmt: skip
def test_a_tax_row_breaking_a_rule_makes_its_possible_sale_ambiguous(tax, reason, amount) -> None:
    """The rejected row is not linked, but the sale it may belong to is not imported with taxes
    0 either: both are added by hand (R-6 of the M5 review, D-160)."""
    m = mapped([tax, sale()])
    entries = by_key(m)
    assert [(r.code, r.as_dict()) for r in entries["line:11"].reasons] == [reason]
    assert entries["order:T1"].category is Category.AMBIGUOUS
    assert [(r.code, r.as_dict()) for r in entries["order:T1"].reasons] == [LOST]
    assert notice(m) == [(TO_ADD[0], TO_ADD[1] | {"amount": amount})]


def test_a_rejected_tax_row_leaves_the_sales_of_other_days_alone() -> None:
    m = mapped([gain_tax(10), sale(value_day="17-04-2025")])
    assert by_key(m)["order:T1"].category is Category.TO_IMPORT
    assert notice(m) == [(TO_ADD[0], TO_ADD[1] | {"amount": "-10"})]


def test_a_rejected_tax_row_leaves_a_sale_with_its_own_withholding_alone() -> None:
    m = mapped([gain_tax(10), sale(), row("Rit. etf", -3)])
    assert by_key(m)["order:T1"].category is Category.TO_IMPORT


def test_a_rejected_tax_row_with_an_unreadable_date_may_be_any_sales() -> None:
    """Like a withholding that cannot be read (D-108): it matches every sale; no notice, which
    needs a date (the row itself is listed with its problem)."""
    m = mapped([gain_tax(day="31-02-2025", value_day="31-02-2025"), sale(ref="T1"),
                sale(ref="T2", asset=STOCK, value_day="18-04-2025")])  # fmt: skip
    entries = by_key(m)
    assert codes(entries["line:11"]) == ["CELL_PROBLEM", "CELL_PROBLEM"]
    assert entries["order:T1"].category is entries["order:T2"].category is Category.AMBIGUOUS
    assert m.notices == ()


def test_a_rejected_and_a_valid_tax_on_the_same_sale() -> None:
    """The sale is ambiguous, so the valid tax cannot be linked: both are added by hand."""
    m = mapped([gain_tax(protocol=90000050), gain_tax(10, protocol=90000052), sale()])
    entries = by_key(m)
    assert codes(entries["order:T1"]) == ["SALE_TAX_ROW_REJECTED"]
    assert codes(entries["line:11"]) == ["SALE_TAX_SALE_REJECTED"]
    assert sorted(n.as_dict()["line"] for n in m.notices) == ["11", "12"]


def test_a_tax_row_inside_an_order_is_ambiguous() -> None:
    entry = only([sale(), row("Ritenuta su plusvalenza", -1)])
    assert [(r.code, r.as_dict()) for r in entry.reasons] == [
        ("ROW_TYPE_NOT_IN_ORDER", {"original_type": "Ritenuta su plusvalenza"})
    ]


def test_do_not_move_cash_keeps_the_linked_sale_to_review() -> None:
    m = map_directa(read_directa(directa_xlsx([gain_tax(), sale()])), move_cash=False)
    (entry,) = m.candidates
    assert entry.category is Category.TO_REVIEW and entry.affects_cash is False
