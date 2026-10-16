"""The synthetic Directa fixture against its expected outcome (M3 "Done when";
docs/import/directa.md §9). Replayed through the engine, as the import will be."""

import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from portfolio_tracker.engine import model as engine
from portfolio_tracker.engine.ledger import replay
from portfolio_tracker.engine.ordering import replay_order
from portfolio_tracker.engine.validation import validate_transaction
from portfolio_tracker.importers.directa_mapping import Category, Entry, Mapped, map_directa
from portfolio_tracker.importers.directa_reader import read_directa

FIXTURES = Path(__file__).parents[3] / "tests/fixtures/directa"
EXPECTED = json.loads((FIXTURES / "directa_synthetic_2024.expected.json").read_text("utf-8"))
T = engine.TransactionType

# Asset types of the fixture's fictitious ISINs (the user confirms them in the preview).
TYPES = {"IE0TEST00020": engine.AssetType.ETF, "IT0TEST00015": engine.AssetType.STOCK,
         "IT0TEST00031": engine.AssetType.BOND}  # fmt: skip
ASSETS = {
    isin: engine.Asset(
        id=uuid.uuid5(uuid.NAMESPACE_URL, isin),
        asset_type=kind,
        quote_currency="EUR",
        quote_unit=engine.QuoteUnit.PERCENT_OF_NOMINAL
        if kind is engine.AssetType.BOND
        else engine.QuoteUnit.CURRENCY,
    )
    for isin, kind in TYPES.items()
}
ISIN_OF = {a.id: isin for isin, a in ASSETS.items()}


def mapped(move_cash: bool = True) -> Mapped:
    data = (FIXTURES / "directa_synthetic_2024.xlsx").read_bytes()
    return map_directa(read_directa(data), move_cash=move_cash)


def to_engine(entry: Entry) -> engine.Transaction:
    asset = ASSETS[entry.isin] if entry.isin else None
    return engine.Transaction(
        id=uuid.uuid5(uuid.NAMESPACE_URL, entry.key),
        type=entry.type,
        trade_date=entry.trade_date,
        value_date=entry.value_date,
        sequence=entry.sequence,
        asset_id=asset.id if asset else None,
        quantity=entry.quantity,
        unit_price=entry.unit_price(bool(asset and asset.is_bond)),
        gross_amount=entry.gross_amount,
        fees=entry.fees,
        accrued_interest_paid=entry.accrued_interest_paid,
        taxes=entry.taxes,
        total_amount=entry.total_amount,
        affects_cash=entry.affects_cash,
    )


def imported(entries: list[Entry]) -> list[engine.Transaction]:
    txs = [to_engine(e) for e in entries]
    for tx in txs:
        asset = next((a for a in ASSETS.values() if a.id == tx.asset_id), None)
        assert validate_transaction(tx, asset) == [], tx
    return txs


def by_seq(m: Mapped) -> dict[int, Entry]:
    return {e.sequence: e for e in m.entries if e.sequence is not None}


def cash_effect(tx: engine.Transaction) -> Decimal:
    if not tx.affects_cash:
        return Decimal(0)
    return -tx.total_amount if tx.type in (T.BUY, T.WITHDRAWAL) else tx.total_amount


def dec(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def test_counts_and_account() -> None:
    m = mapped()
    assert m.account_code == EXPECTED["account_code"]
    assert sum(len(e.lines) for e in m.entries) == EXPECTED["rows_in_file"]
    assert sum(e.selectable for e in m.entries) == EXPECTED["transactions_after_merge"]
    assert m.counts() == {Category.TO_IMPORT: 11, Category.TO_REVIEW: 1, Category.NOT_RECOGNIZED: 1}
    assert m.bond_isins == {"IT0TEST00031"}


def test_default_selection_leaves_the_disaggio_order_to_review() -> None:
    m, expected = mapped(), EXPECTED["default_selection"]
    default = [e for e in m.entries if e.selected_by_default]
    assert sorted(e.sequence for e in default) == expected["to_import_seq"]
    review = [e for e in m.entries if e.category is Category.TO_REVIEW]
    assert [
        {"seq": e.sequence, "external_ref": e.external_ref,
         "reasons": [{"code": r.code, **r.as_dict()} for r in e.reasons]}
        for e in review
    ] == expected["to_review"]  # fmt: skip
    assert all(e.selectable and not e.selected_by_default for e in review)

    ledger = replay(imported(default))
    assert len(default) == expected["transactions_imported"]
    assert ledger.cash == Decimal(expected["final_cash"])
    positions = {ISIN_OF[a]: p for a, p in ledger.positions.items()}
    assert {
        isin: {"quantity": p.quantity, "cost_basis": p.cost_basis} for isin, p in positions.items()
    } == {
        isin: {
            "quantity": Decimal(v.get("quantity") or v["quantity_nominal"]),
            "cost_basis": Decimal(v["cost_basis"]),
        }
        for isin, v in expected["positions"].items()
    }
    # The bond coupon stays selected while its purchase is to review: the engine warns.
    assert [(w.code.value, w.date.isoformat()) for w in ledger.warnings] == [
        (w["code"], w["date"]) for w in expected["warnings"]
    ]


def test_all_transactions_after_selecting_the_order_to_review() -> None:
    """The 12 transactions of the expected JSON, in replay order, with running cash."""
    m = mapped()
    entries = by_seq(m)
    txs = imported([e for e in m.entries if e.selectable])
    cash = Decimal(0)
    for order, (tx, exp) in enumerate(
        zip(replay_order(txs), EXPECTED["transactions"], strict=True)
    ):
        entry = entries[tx.sequence]
        cash += cash_effect(tx)
        assert exp["replay_order"] == order + 1
        actual = {
            "seq": tx.sequence,
            "type": tx.type.value,
            "trade_date": tx.trade_date.isoformat(),
            "value_date": tx.value_date.isoformat(),
            "isin": entry.isin,
            "external_ref": entry.external_ref,
            "quantity": tx.quantity,
            "unit_price": tx.unit_price,
            "gross_amount": tx.gross_amount,
            "fees": tx.fees,
            "accrued_interest_paid": tx.accrued_interest_paid,
            "taxes": tx.taxes,
            "total_amount": tx.total_amount,
            "notes": entry.notes,
            "metadata": dict(entry.metadata) if entry.metadata else None,
            "affects_cash": tx.affects_cash,
            "source_rows": len(entry.lines),
            "cash_after": cash,
        }
        wanted = {
            key: exp.get(key)
            for key in actual
            if key not in {"quantity", "unit_price", "gross_amount", "fees",
                           "accrued_interest_paid", "taxes", "total_amount", "cash_after"}
        } | {
            key: dec(exp.get(key))
            for key in ("quantity", "unit_price", "gross_amount", "fees",
                        "accrued_interest_paid", "taxes", "total_amount", "cash_after")
        }  # fmt: skip
        assert actual == wanted, exp["seq"]
        if exp.get("pairing") == "consecutive_protocol":
            assert [r.code for r in entry.info] == ["WITHHOLDING_PAIRED"]

    ledger = replay(txs)
    assert ledger.cash == Decimal(EXPECTED["final_cash"])
    assert ledger.warnings == ()
    positions = {ISIN_OF[a]: p for a, p in ledger.positions.items()}
    for isin, exp in EXPECTED["positions"].items():
        quantity = exp.get("quantity") or exp["quantity_nominal"]
        assert positions[isin].quantity == Decimal(quantity)
        assert positions[isin].cost_basis == Decimal(exp["cost_basis"])
    bond = positions["IT0TEST00031"]
    income = EXPECTED["bond_asset_income_net_of_accrued"]
    assert bond.income_gross == Decimal(income["gross_income"])
    assert bond.income_taxes == Decimal(income["taxes"])
    assert bond.income_gross - bond.income_taxes == Decimal(income["net_income"])


def test_merged_trades() -> None:
    entries = {e.external_ref: e for e in mapped().entries if e.type is T.BUY}
    for ref, exp in EXPECTED["merged_trades"].items():
        entry = entries[ref]
        is_bond = entry.isin in mapped().bond_isins
        assert entry.isin == exp["isin"]
        assert entry.quantity == Decimal(exp.get("quantity") or exp["nominal"])
        assert entry.unit_price(is_bond) == Decimal(exp.get("unit_price") or exp["price_percent"])
        assert entry.fees == Decimal(exp["fees"])
        assert -entry.total_amount == Decimal(exp["cash_effect"])
        if "taxes_signed" in exp:
            assert entry.taxes == Decimal(exp["taxes_signed"])
            assert entry.accrued_interest_paid == Decimal(exp["accrued_interest_paid"])
            assert entry.gross_amount + entry.fees == Decimal(exp["cost_basis"])
    assert [r.code for r in entries["T0000000000003"].info] == ["MERGED_EXECUTIONS"]


def test_unrecognized_row() -> None:
    m = mapped()
    file = read_directa((FIXTURES / "directa_synthetic_2024.xlsx").read_bytes())
    rows = {r.line: r for r in file.rows}
    rejected = [e for e in m.entries if e.category is Category.NOT_RECOGNIZED]
    assert len(rejected) == len(EXPECTED["unrecognized_rows"]) == 1
    entry, exp = rejected[0], EXPECTED["unrecognized_rows"][0]
    row = rows[entry.lines[0]]
    assert (row.movement, row.protocol, row.amount) == (
        exp["type"], str(exp["protocol"]), Decimal(exp["amount"])
    )  # fmt: skip
    assert [(r.code, r.as_dict()) for r in entry.reasons] == [
        ("MOVEMENT_TYPE_NOT_VERIFIED", {"original_type": exp["type"]})
    ]


def test_account_expenses() -> None:
    expenses = [e for e in mapped().entries if e.account_expense]
    assert [
        {"note": e.notes, "amount": -e.total_amount, "date": e.trade_date.isoformat()}
        for e in expenses
    ] == [
        {"note": x["note"], "amount": Decimal(x["amount"]), "date": x["date"]}
        for x in EXPECTED["account_expenses_imported_as_withdrawal"]
    ]


@pytest.mark.parametrize("select_review", [False, True])
def test_do_not_move_cash(select_review: bool) -> None:
    m, exp = mapped(move_cash=False), EXPECTED["do_not_move_cash_option"]
    assert (
        sorted(e.sequence for e in m.entries if e.selected_by_default)
        == exp["default_selected_seq"]
    )
    skipped = [e for e in m.entries if e.category is Category.SKIPPED_BY_CHOICE]
    assert sorted(e.sequence for e in skipped) == exp["skipped_by_default_seq"]
    assert all(e.selectable and not e.selected_by_default for e in skipped)
    review = [e.sequence for e in m.entries if e.category is Category.TO_REVIEW]
    assert review == exp["to_review_seq"]

    chosen = [
        e
        for e in m.entries
        if e.selected_by_default or (select_review and e.category is Category.TO_REVIEW)
    ]
    if select_review:
        assert sorted(e.sequence for e in chosen) == exp["imported_seq"]
        assert len(chosen) == exp["transactions_imported"]
    assert all(not e.affects_cash for e in chosen)
    ledger = replay(imported(chosen))
    assert ledger.cash == Decimal(exp["final_cash"])
    positions = {ISIN_OF[a]: p for a, p in ledger.positions.items()}
    warnings = [(w.code.value, w.date.isoformat()) for w in ledger.warnings]
    if not select_review:
        assert warnings == [(w["code"], w["date"]) for w in exp["default_warnings"]]
    else:
        assert warnings == []
    for isin, want in exp["positions"].items():
        if isin == "IT0TEST00031" and not select_review:
            assert positions[isin].quantity == 0  # coupon only (D-72)
            continue
        quantity = want.get("quantity") or want["quantity_nominal"]
        assert positions[isin].quantity == Decimal(quantity)
        assert positions[isin].cost_basis == Decimal(want["cost_basis"])


def test_option_skipped_rows_can_be_selected_back() -> None:
    """Selecting the skipped deposits and expenses again gives back the full cash history,
    except that trades and income still do not move cash."""
    m = mapped(move_cash=False)
    deposits_and_expenses = [e for e in m.entries if e.category is Category.SKIPPED_BY_CHOICE]
    ledger = replay(imported(deposits_and_expenses))
    assert ledger.cash == Decimal("8000.00") - Decimal("0.50") - Decimal("6.20")
