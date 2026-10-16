"""Import service, preview (D-38, D-99 to D-112; docs/import/). Synthetic files only: the
Directa fixture of tests/fixtures and CSV text built here, with invented accounts and ISINs."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from portfolio_tracker.api.errors import ApiError
from portfolio_tracker.db import models
from portfolio_tracker.engine.model import AssetType, QuoteUnit
from portfolio_tracker.importers.base import Category
from portfolio_tracker.repositories import accounts, assets, users
from portfolio_tracker.repositories import transactions as txs
from portfolio_tracker.services import imports
from portfolio_tracker.services.imports import AssetChoice, ImportInput, Preview

FIXTURES = Path(__file__).parents[2] / "tests/fixtures/directa"
XLSX = (FIXTURES / "directa_synthetic_2024.xlsx").read_bytes()
EXPECTED = json.loads((FIXTURES / "directa_synthetic_2024.expected.json").read_text("utf-8"))
TODAY = date(2026, 1, 15)
ETF, STOCK, BOND = "isin:IE0TEST00020", "isin:IT0TEST00015", "isin:IT0TEST00031"
CHOICES = {ETF: AssetChoice(asset_type=AssetType.ETF),
           STOCK: AssetChoice(asset_type=AssetType.STOCK)}  # fmt: skip
BOND_ORDER = "order:T0000000000004"
COUPON = "protocol:90000007"
CSV_HEADER = "account,account_currency,trade_date,type,isin,ticker,asset_name,asset_type,"
CSV_HEADER += "asset_quote_currency,quantity,unit_price,gross_amount,fees,total_amount,"
CSV_HEADER += "external_ref,id"


def user(db: Session, name: str) -> models.User:
    return users.add_user(db, username=name, role="INVESTOR", password_hash="x", language="en")


def account(db: Session, owner: models.User, name: str = "Broker", currency: str = "EUR",
            code: str | None = None) -> models.Account:  # fmt: skip
    return accounts.add_account(db, owner.id, name=name, currency=currency, external_code=code)


def private_asset(db: Session, owner: models.User, **fields) -> models.Asset:
    fields = {"name": "Example ETF", "asset_type": "ETF", "quote_currency": "EUR",
              "quote_unit": "CURRENCY", **fields}  # fmt: skip
    return assets.add_private_asset(db, owner.id, **fields)


def add_tx(db: Session, acct: models.Account, *, type: str = "DEPOSIT", day=date(2024, 1, 2),
           ref: str | None = None, **amounts) -> models.Transaction:  # fmt: skip
    row = models.Transaction(
        account_id=acct.id, type=type, trade_date=day, external_ref=ref,
        sequence=txs.next_sequence(db, acct.id),
        total_amount=amounts.pop("total_amount", Decimal("100.00")), **amounts,
    )  # fmt: skip
    db.add(row)
    db.flush()
    return row


def directa(db: Session, owner: models.User, acct: models.Account | None, **inputs) -> Preview:
    inputs.setdefault("assets", CHOICES)
    inputs.setdefault("file_name", "directa.xlsx")
    inp = ImportInput(content=XLSX, account_id=acct.id if acct else None, **inputs)
    return imports.preview(db, owner.id, inp, today=TODAY)


def csv_file(*rows: str, header: str = CSV_HEADER) -> bytes:
    return ("\n".join((header, *rows)) + "\n").encode()


def csv_preview(db: Session, owner: models.User, data: bytes, **inputs) -> Preview:
    return imports.preview(db, owner.id, ImportInput(content=data, **inputs), today=TODAY)


def keys(p: Preview, category: Category) -> list[str]:
    return [line.key for line in p.lines if line.candidate.category is category]


def selected_seqs(p: Preview) -> list[int]:
    return sorted(line.candidate.sequence for line in p.lines if line.selected)


def line(p: Preview, key: str) -> imports.Line:
    return next(x for x in p.lines if x.key == key)


def codes(reasons) -> list[str]:
    return [r.code for r in reasons]


@pytest.fixture
def alice(db_session: Session) -> models.User:
    return user(db_session, "alice")


# --- Directa: the fixture ---------------------------------------------------------------------


def test_fixture_default_selection_matches_expected(db_session, alice) -> None:
    p = directa(db_session, alice, account(db_session, alice))
    expected = EXPECTED["default_selection"]
    assert p.source == "directa-xlsx" and p.account_code == EXPECTED["account_code"]
    assert selected_seqs(p) == expected["to_import_seq"]
    assert keys(p, Category.TO_REVIEW) == [BOND_ORDER]
    assert [line(p, BOND_ORDER).candidate.sequence] == [t["seq"] for t in expected["to_review"]]
    assert p.count(Category.NOT_RECOGNIZED) == len(EXPECTED["unrecognized_rows"])
    assert p.blocking == ()
    assert [(w.code, w.date.isoformat(), w.line) for w in p.warnings] == [
        (w["code"], w["date"], COUPON) for w in expected["warnings"]
    ]
    assert all(w.introduced for w in p.warnings)


def test_new_assets_are_proposed_from_the_file(db_session, alice) -> None:
    """D-102: BOND only on evidence; the other types are chosen by the user."""
    p = directa(db_session, alice, account(db_session, alice), assets={})
    plans = {a.key: a for a in p.assets}
    assert list(plans) == [ETF, STOCK, BOND]  # file order: oldest row last
    assert all(a.new and a.quote_currency == "EUR" for a in plans.values())
    assert (plans[BOND].asset_type, plans[BOND].quote_unit) == (
        AssetType.BOND, QuoteUnit.PERCENT_OF_NOMINAL)  # fmt: skip
    assert plans[ETF].asset_type is None and plans[ETF].isin == "IE0TEST00020"
    assert [(r.code, r.as_dict()) for r in p.blocking] == [
        ("ASSET_TYPE_REQUIRED", {"asset": ETF}),
        ("ASSET_TYPE_REQUIRED", {"asset": STOCK}),
    ]
    assert p.warnings == ()  # no dry run while a type is missing


def test_unit_price_follows_the_chosen_type(db_session, alice) -> None:
    """Directa gives no unit price (§3): per unit, or % of nominal for a bond."""
    acct = account(db_session, alice)
    etf = directa(db_session, alice, acct)
    price = line(etf, "order:T0000000000001").candidate.unit_price
    as_bond = directa(db_session, alice, acct, assets={**CHOICES, ETF: AssetChoice(
        asset_type=AssetType.BOND)})  # fmt: skip
    assert line(as_bond, "order:T0000000000001").candidate.unit_price == price * 100
    assert next(a for a in as_bond.assets if a.key == ETF).quote_unit is (
        QuoteUnit.PERCENT_OF_NOMINAL)  # fmt: skip


def test_related_order_to_review_note(db_session, alice) -> None:
    """D-99 note: the coupon is selected while the purchase of its bond is still to review."""
    acct = account(db_session, alice)
    p = directa(db_session, alice, acct)
    notes = [(r.code, r.as_dict()) for r in line(p, COUPON).candidate.info]
    assert ("RELATED_ORDER_TO_REVIEW",
            {"order_key": BOND_ORDER, "external_ref": "T0000000000004"}) in notes  # fmt: skip
    assert all("RELATED_ORDER_TO_REVIEW" not in codes(x.candidate.info)
               for x in p.lines if x.key != COUPON)  # fmt: skip

    default = {x.key for x in p.lines if x.selected}
    ticked = directa(db_session, alice, acct, selected=frozenset(default | {BOND_ORDER}))
    assert "RELATED_ORDER_TO_REVIEW" not in codes(line(ticked, COUPON).candidate.info)
    assert ticked.warnings == ()  # the coupon now has its holding
    assert selected_seqs(ticked) == sorted(t["seq"] for t in EXPECTED["transactions"])


def test_do_not_move_cash_default_selection(db_session, alice) -> None:
    p = directa(db_session, alice, account(db_session, alice), move_cash=False)
    expected = EXPECTED["do_not_move_cash_option"]
    assert selected_seqs(p) == expected["default_selected_seq"]
    skipped = [line(p, k).candidate.sequence for k in keys(p, Category.SKIPPED_BY_CHOICE)]
    assert sorted(skipped) == expected["skipped_by_default_seq"]
    assert [(w.code, w.date.isoformat()) for w in p.warnings] == [
        (w["code"], w["date"]) for w in expected["default_warnings"]
    ]
    assert all(not x.candidate.affects_cash for x in p.lines if x.selected and x.asset_key)


# --- Directa: accounts (§4) -------------------------------------------------------------------


def test_account_found_by_its_remembered_code(db_session, alice) -> None:
    account(db_session, alice, "Other")
    acct = account(db_session, alice, code=EXPECTED["account_code"])
    p = directa(db_session, alice, None)
    assert p.accounts[0].account_id == acct.id and p.notices == ()


def test_account_is_required_without_a_remembered_code(db_session, alice) -> None:
    account(db_session, alice)
    with pytest.raises(ApiError) as exc:
        directa(db_session, alice, None)
    assert (exc.value.status, exc.value.code) == (422, "IMPORT_ACCOUNT_REQUIRED")


def test_a_different_account_code_is_a_notice(db_session, alice) -> None:
    p = directa(db_session, alice, account(db_session, alice, code="B9999"))
    assert [(r.code, r.as_dict()) for r in p.notices] == [
        ("ACCOUNT_CODE_DIFFERS", {"file_code": "B0000", "account_code": "B9999"})
    ]


def test_a_non_eur_account_is_refused(db_session, alice) -> None:
    with pytest.raises(ApiError) as exc:
        directa(db_session, alice, account(db_session, alice, currency="USD"))
    assert (exc.value.status, exc.value.code) == (422, "IMPORT_ACCOUNT_CURRENCY")
    assert exc.value.extra == {"account_currency": "USD"}


def test_another_users_account_is_not_found(db_session, alice) -> None:
    bob = user(db_session, "bob")
    with pytest.raises(ApiError) as exc:
        directa(db_session, alice, account(db_session, bob))
    assert (exc.value.status, exc.value.code) == (404, "NOT_FOUND")


# --- Directa: duplicates and notices (§6, D-104) ------------------------------------------------


def test_already_present_by_reference_and_type(db_session, alice) -> None:
    acct = account(db_session, alice)
    add_tx(db_session, acct, ref="90000001", total_amount=Decimal("5000.00"))  # same deposit
    add_tx(db_session, acct, ref="90000003", type="WITHDRAWAL")  # same ref, other type
    p = directa(db_session, alice, acct)
    assert keys(p, Category.DUPLICATE) == ["protocol:90000001"]
    assert [(r.code, r.as_dict()) for r in line(p, "protocol:90000001").candidate.reasons] == [
        ("ALREADY_PRESENT", {"match": "external_ref_and_type"})
    ]
    assert line(p, "protocol:90000003").selected


def test_manual_entries_inside_the_file_dates_are_a_notice(db_session, alice) -> None:
    acct = account(db_session, alice)
    add_tx(db_session, acct, day=date(2024, 5, 2))  # by hand: no external_ref
    add_tx(db_session, acct, day=date(2023, 5, 2))  # outside the file's dates
    p = directa(db_session, alice, acct)
    assert [(r.code, r.as_dict()) for r in p.notices] == [
        ("MANUAL_OVERLAP", {"account": "", "count": "1", "first_date": "2024-01-10",
                            "last_date": "2024-09-16"})
    ]  # fmt: skip


def test_an_asset_the_user_has_is_used(db_session, alice) -> None:
    owned = private_asset(db_session, alice, isin="IE0TEST00020")
    bob = user(db_session, "bob")
    private_asset(db_session, bob, isin="IT0TEST00015", asset_type="STOCK")  # not Alice's
    p = directa(db_session, alice, account(db_session, alice), assets={STOCK: CHOICES[STOCK]})
    plans = {a.key: a for a in p.assets}
    assert plans[ETF].asset_id == owned.id and not plans[ETF].new
    assert plans[STOCK].new and p.blocking == ()


def test_choices_apply_to_new_assets_only(db_session, alice) -> None:
    """A choice that needs no making is ignored (an asset created meanwhile, say); mapping an
    asset with an ISIN to another asset is refused."""
    owned = private_asset(db_session, alice, isin="IE0TEST00020")
    acct = account(db_session, alice)
    stale = {**CHOICES, "isin:IT0000000000": CHOICES[ETF]}
    p = directa(db_session, alice, acct, assets=stale)
    assert next(a for a in p.assets if a.key == ETF).asset_id == owned.id
    assert (
        p.fingerprint
        == directa(db_session, alice, acct, assets={STOCK: CHOICES[STOCK]}).fingerprint
    )
    with pytest.raises(ApiError) as exc:
        directa(db_session, alice, acct, assets={STOCK: AssetChoice(asset_id=owned.id)})
    assert (exc.value.status, exc.value.code) == (422, "IMPORT_CHOICE_INVALID")


def test_only_selectable_lines_can_be_selected(db_session, alice) -> None:
    p = directa(db_session, alice, account(db_session, alice))
    unrecognized = keys(p, Category.NOT_RECOGNIZED)[0]
    with pytest.raises(ApiError) as exc:
        directa(db_session, alice, account(db_session, alice, "B"),
                selected=frozenset({unrecognized}))  # fmt: skip
    assert (exc.value.code, exc.value.extra) == ("IMPORT_SELECTION_INVALID",
                                                 {"lines": [unrecognized]})  # fmt: skip


# --- Fingerprint (D-100) ----------------------------------------------------------------------


def test_fingerprint_is_stable_and_follows_inputs_and_ledger(db_session, alice) -> None:
    acct = account(db_session, alice)
    first = directa(db_session, alice, acct)
    assert directa(db_session, alice, acct).fingerprint == first.fingerprint
    reselected = directa(db_session, alice, acct, selected=frozenset({COUPON}))
    assert reselected.fingerprint == first.fingerprint  # the selection is sent at confirm
    assert directa(db_session, alice, acct, move_cash=False).fingerprint != first.fingerprint
    bond = {**CHOICES, ETF: AssetChoice(asset_type=AssetType.STOCK)}
    assert directa(db_session, alice, acct, assets=bond).fingerprint != first.fingerprint
    add_tx(db_session, acct, day=date(2020, 1, 2))  # any change to the target ledger
    assert directa(db_session, alice, acct).fingerprint != first.fingerprint


# --- App CSV ----------------------------------------------------------------------------------


def test_csv_accounts_are_proposed_or_created(db_session, alice) -> None:
    main = account(db_session, alice, "Main")
    account(db_session, alice, "Main", currency="USD")
    data = csv_file("Main,EUR,2024-01-10,DEPOSIT,,,,,,,,,,100,,",
                    "Dollars,USD,2024-01-10,DEPOSIT,,,,,,,,,,50,,",
                    "Bare,,2024-01-11,DEPOSIT,,,,,,,,,,1,,")  # fmt: skip
    p = csv_preview(db_session, alice, data)
    assert [(a.key, a.account_id, a.currency) for a in p.accounts] == [
        ("Main", main.id, "EUR"), ("Dollars", None, "USD"), ("Bare", None, None)
    ]  # fmt: skip
    assert [(r.code, r.as_dict()) for r in p.blocking] == [
        ("ACCOUNT_CURRENCY_REQUIRED", {"account": "Bare"})
    ]
    mapped = csv_preview(db_session, alice, data, accounts={"Main": None, "Bare": main.id})
    assert [(a.key, a.account_id) for a in mapped.accounts] == [
        ("Main", None), ("Dollars", None), ("Bare", main.id)
    ]  # fmt: skip
    assert mapped.blocking == ()


def test_csv_row_currency_must_match_its_account(db_session, alice) -> None:
    dollars = account(db_session, alice, "Main", currency="USD")
    data = csv_file("Main,EUR,2024-01-10,DEPOSIT,,,,,,,,,,100,,")
    p = csv_preview(db_session, alice, data, accounts={"Main": dollars.id})
    assert [(r.code, r.as_dict()) for r in p.lines[0].candidate.reasons] == [
        ("ACCOUNT_CURRENCY_MISMATCH", {"currency": "EUR", "account_currency": "USD"})
    ]
    assert p.lines[0].candidate.category is Category.INVALID


def test_csv_unknown_account_mapping_is_refused(db_session, alice) -> None:
    with pytest.raises(ApiError) as exc:
        csv_preview(db_session, alice, csv_file("Main,EUR,2024-01-10,DEPOSIT,,,,,,,,,,1,,"),
                    accounts={"Elsewhere": None})  # fmt: skip
    assert exc.value.code == "IMPORT_CHOICE_INVALID"


def test_csv_duplicates_by_id_then_by_reference(db_session, alice) -> None:
    main = account(db_session, alice, "Main")
    mine = add_tx(db_session, main, day=date(2024, 1, 3))
    add_tx(db_session, main, ref="R-1", day=date(2024, 1, 4))
    bob = user(db_session, "bob")
    theirs = add_tx(db_session, account(db_session, bob))
    data = csv_file(f"Main,EUR,2024-01-03,DEPOSIT,,,,,,,,,,100,,{mine.id}",
                    "Main,EUR,2024-01-04,DEPOSIT,,,,,,,,,,100,R-1,",
                    "Main,EUR,2024-01-04,WITHDRAWAL,,,,,,,,,,100,R-1,",
                    f"Main,EUR,2024-01-05,DEPOSIT,,,,,,,,,,100,,{theirs.id}")  # fmt: skip
    work = imports._build(db_session, alice.id, ImportInput(content=data), TODAY)
    p = work.preview
    assert [(x.key, x.candidate.category, codes(x.candidate.reasons)) for x in p.lines] == [
        ("line:2", Category.DUPLICATE, ["ALREADY_PRESENT"]),
        ("line:3", Category.DUPLICATE, ["ALREADY_PRESENT"]),
        ("line:4", Category.TO_IMPORT, []),
        ("line:5", Category.TO_IMPORT, []),  # another user's id: nothing revealed
    ]
    assert [r.as_dict() for x in p.lines[:2] for r in x.candidate.reasons] == [
        {"match": "id"}, {"match": "external_ref"}
    ]  # fmt: skip
    assert work.ids["line:5"] != theirs.id  # a new id, silently (D-38)


def test_csv_keeps_its_own_new_ids(db_session, alice) -> None:
    given = UUID("00000000-0000-4000-8000-000000000001")
    data = csv_file(f"Main,EUR,2024-01-03,DEPOSIT,,,,,,,,,,100,,{given}")
    work = imports._build(db_session, alice.id, ImportInput(content=data), TODAY)
    assert work.ids == {"line:2": given}


def test_csv_asset_by_ticker_without_isin(db_session, alice) -> None:
    """app-csv.md "Assets": without an ISIN the user's assets with that ticker are offered."""
    one = private_asset(db_session, alice, ticker="EXA", name="Example A")
    data = csv_file("Main,EUR,2024-01-10,BUY,,EXA,Example A,ETF,EUR,10,5,50,0,50,,")
    p = csv_preview(db_session, alice, data)
    assert [(a.key, a.asset_id, a.options) for a in p.assets] == [("ticker:EXA", one.id, (one.id,))]
    assert p.blocking == ()

    two = private_asset(db_session, alice, ticker="EXA", name="Example B")
    p = csv_preview(db_session, alice, data)
    assert p.assets[0].new and set(p.assets[0].options) == {one.id, two.id}
    assert [(r.code, r.as_dict()) for r in p.blocking] == [
        ("ASSET_CHOICE_REQUIRED", {"asset": "ticker:EXA"})
    ]
    pick = {"ticker:EXA": AssetChoice(asset_id=two.id)}
    chosen = csv_preview(db_session, alice, data, assets=pick)
    assert chosen.assets[0].asset_id == two.id and chosen.blocking == ()
    new = csv_preview(db_session, alice, data, assets={"ticker:EXA": AssetChoice()})
    assert new.assets[0].new and new.blocking == ()  # created from the file's columns


def test_csv_new_asset_from_its_columns(db_session, alice) -> None:
    """D-111: the asset columns define a new asset; the unit follows the type when missing."""
    data = csv_file("Main,EUR,2024-01-10,BUY,IE0TEST00020,EXA,Example A,ETF,EUR,10,5,50,0,50,,")
    plan = csv_preview(db_session, alice, data).assets[0]
    assert (plan.key, plan.new, plan.asset_type, plan.quote_currency, plan.quote_unit) == (
        ETF, True, AssetType.ETF, "EUR", QuoteUnit.CURRENCY)  # fmt: skip
    no_currency = data.replace(b"ETF,EUR,", b"ETF,,")
    assert codes(csv_preview(db_session, alice, no_currency).blocking) == [
        "ASSET_CURRENCY_REQUIRED"
    ]
    bad = csv_preview(db_session, alice, data, assets={
        ETF: AssetChoice(quote_unit=QuoteUnit.PERCENT_OF_NOMINAL)})  # fmt: skip
    assert [(r.code, r.as_dict()) for r in bad.blocking] == [
        ("ASSET_INVALID", {"asset": ETF, "field": "quote_unit", "rule": "must_match_asset_type"})
    ]


def test_csv_asset_columns_differing_from_the_existing_asset(db_session, alice) -> None:
    private_asset(db_session, alice, isin="IE0TEST00020")  # an ETF quoted in EUR
    data = csv_file("Main,EUR,2024-01-10,BUY,IE0TEST00020,,,STOCK,EUR,10,5,50,0,50,,")
    p = csv_preview(db_session, alice, data)
    assert [(r.code, r.as_dict()) for r in p.notices] == [
        ("ASSET_DEFINITION_DIFFERS", {"asset": ETF, "field": "asset_type"})
    ]


def test_csv_field_rules_make_a_line_invalid(db_session, alice) -> None:
    """The engine's rules (domain-rules §2) run in the preview: such a line is not selectable."""
    data = csv_file("Main,EUR,2024-01-10,BUY,IE0TEST00020,,,ETF,EUR,10,5,50,0,49,,")
    p = csv_preview(db_session, alice, data)
    assert p.lines[0].candidate.category is Category.INVALID and not p.lines[0].selected
    assert [r.as_dict() for r in p.lines[0].candidate.reasons] == [
        {"field": "total_amount", "rule": "invariant"}
    ]


def test_csv_quantity_conflict_blocks(db_session, alice) -> None:
    data = csv_file("Main,EUR,2024-01-10,BUY,IE0TEST00020,,,ETF,EUR,10,5,50,0,50,,",
                    "Main,EUR,2024-01-11,SELL,IE0TEST00020,,,ETF,EUR,11,5,55,0,55,,")  # fmt: skip
    p = csv_preview(db_session, alice, data)
    assert [(r.code, r.as_dict()) for r in p.blocking] == [
        ("QUANTITY_CONFLICT", {"reason": "NEGATIVE_QUANTITY", "account": "Main",
                               "line": "line:3", "quantity": "-1", "transaction_id": ""})
    ]  # fmt: skip


def test_csv_warnings_name_their_line(db_session, alice) -> None:
    data = csv_file("Main,EUR,2024-01-10,WITHDRAWAL,,,,,,,,,,100,,")
    p = csv_preview(db_session, alice, data)
    assert [(w.code, w.account_key, w.line, w.introduced) for w in p.warnings] == [
        ("NEGATIVE_CASH", "Main", None, True)
    ]


def test_csv_move_cash_option(db_session, alice) -> None:
    """D-112: with the option, deposits are skipped by default and trades do not move cash."""
    data = csv_file("Main,EUR,2024-01-09,DEPOSIT,,,,,,,,,,100,,",
                    "Main,EUR,2024-01-10,BUY,IE0TEST00020,,,ETF,EUR,10,5,50,0,50,,")  # fmt: skip
    p = csv_preview(db_session, alice, data, move_cash=False)
    assert [(x.candidate.category, x.selected, x.candidate.affects_cash) for x in p.lines] == [
        (Category.SKIPPED_BY_CHOICE, False, True), (Category.TO_IMPORT, True, False)
    ]  # fmt: skip
    assert p.warnings == ()  # no cash moved: nothing goes negative


def test_unreadable_file_is_an_api_error(db_session, alice) -> None:
    with pytest.raises(ApiError) as exc:
        csv_preview(db_session, alice, b"not,a,known\nheader,at,all\n")
    assert (exc.value.status, exc.value.code) == (422, "IMPORT_FILE_INVALID")
    with pytest.raises(ApiError) as exc:
        csv_preview(db_session, alice, b"x", source="no-such-format")
    assert exc.value.code == "IMPORT_FORMAT_UNKNOWN"


# --- Confirm ----------------------------------------------------------------------------------


def confirm(db: Session, owner: models.User, inp: ImportInput, fingerprint: str | None = None):
    if fingerprint is None:
        fingerprint = imports.preview(db, owner.id, inp, today=TODAY).fingerprint
    return imports.confirm(db, owner.id, owner.id, inp, fingerprint, today=TODAY, ip="198.51.100.7")


def directa_input(acct: models.Account | None, **inputs) -> ImportInput:
    inputs.setdefault("assets", CHOICES)
    return ImportInput(content=XLSX, file_name="directa.xlsx",
                       account_id=acct.id if acct else None, **inputs)  # fmt: skip


def ledger_of(db: Session, owner: models.User, acct_id: UUID):
    from portfolio_tracker.engine.ledger import replay
    from portfolio_tracker.repositories import mapping

    rows = txs.account_ledger(db, owner.id, acct_id)
    return rows, replay([mapping.to_engine_transaction(r) for r in rows])


def positions_by_isin(db: Session, ledger) -> dict[str, tuple[Decimal, Decimal]]:
    found = assets.assets_by_id(db, set(ledger.positions))
    return {found[a].isin: (p.quantity, p.cost_basis) for a, p in ledger.positions.items()}


def expected_positions(block: dict) -> dict[str, tuple[Decimal, Decimal]]:
    return {
        isin: (Decimal(v.get("quantity", v.get("quantity_nominal"))), Decimal(v["cost_basis"]))
        for isin, v in block.items()
    }


def audit_actions(db: Session) -> list[str]:
    from sqlalchemy import select

    return list(db.scalars(select(models.AuditEntry.action).order_by(models.AuditEntry.at)))


def test_confirm_fixture_default_selection(db_session, alice) -> None:
    acct = account(db_session, alice)
    done = confirm(db_session, alice, directa_input(acct))
    expected = EXPECTED["default_selection"]
    assert len(done.created) == expected["transactions_imported"] == 11
    rows, ledger = ledger_of(db_session, alice, acct.id)
    assert ledger.cash == Decimal(expected["final_cash"])
    assert positions_by_isin(db_session, ledger) == expected_positions(expected["positions"])
    assert [r.sequence for r in rows] == list(range(1, 12))  # renumbered without the gap
    assert [(w.code, w.date.isoformat()) for w in done.warnings] == [
        (w["code"], w["date"]) for w in expected["warnings"]
    ]

    (batch,) = done.batches
    assert {r.import_batch_id for r in rows} == {batch.id}
    assert (batch.account_id, batch.source, batch.file_name, batch.move_cash) == (
        acct.id, "directa-xlsx", "directa.xlsx", True)  # fmt: skip
    assert (
        batch.imported,
        batch.duplicates,
        batch.not_recognized,
        batch.to_review_skipped,
        batch.ambiguous,
        batch.invalid,
        batch.skipped_by_choice,
    ) == (11, 0, 1, 1, 0, 0, 0)
    assert len(done.assets_created) == 3  # the bond too: the coupon uses it  # fmt: skip
    created = assets.assets_by_id(db_session, set(done.assets_created)).values()
    assert {(a.isin, a.asset_type, a.owner_id) for a in created} == {
        ("IE0TEST00020", "ETF", alice.id), ("IT0TEST00015", "STOCK", alice.id),
        ("IT0TEST00031", "BOND", alice.id),
    }  # fmt: skip
    assert acct.external_code == EXPECTED["account_code"]  # remembered (§4)
    assert audit_actions(db_session) == ["import.confirmed"]  # one entry, not one per row


def test_audit_entry_of_the_batch(db_session, alice) -> None:
    acct = account(db_session, alice)
    done = confirm(db_session, alice, directa_input(acct))
    from sqlalchemy import select

    entry = db_session.scalars(select(models.AuditEntry)).one()
    assert (entry.entity_type, entry.entity_id, entry.actor_user_id, entry.ip) == (
        "import_batch", str(done.batches[0].id), alice.id, "198.51.100.7")  # fmt: skip
    assert entry.after["imported"] == 11 and entry.after["account_code_remembered"] is True
    assert sorted(entry.after["assets_created"]) == sorted(map(str, done.assets_created))
    assert "B0000" not in json.dumps(entry.after) and "directa.xlsx" not in json.dumps(entry.after)


def test_reimport_imports_nothing(db_session, alice) -> None:
    """M3 "Done when": a re-import imports nothing (directa.md §6)."""
    acct = account(db_session, alice)
    confirm(db_session, alice, directa_input(acct))
    again = directa(db_session, alice, None)  # found by the remembered account code
    assert again.selected_count == 0 and again.count(Category.DUPLICATE) == 11
    assert keys(again, Category.TO_REVIEW) == [BOND_ORDER]  # still to review, not imported
    assert again.assets and all(not a.new for a in again.assets)
    done = confirm(db_session, alice, directa_input(None, assets={}))
    assert done.created == () and done.batches[0].imported == 0
    assert done.batches[0].duplicates == 11
    assert len(txs.account_ledger(db_session, alice.id, acct.id)) == 11


def test_confirm_with_the_order_to_review_selected(db_session, alice) -> None:
    acct = account(db_session, alice)
    default = {x.key for x in directa(db_session, alice, acct).lines if x.selected}
    done = confirm(
        db_session, alice, directa_input(acct, selected=frozenset(default | {BOND_ORDER}))
    )
    assert len(done.created) == EXPECTED["transactions_after_merge"]
    rows, ledger = ledger_of(db_session, alice, acct.id)
    assert ledger.cash == Decimal(EXPECTED["final_cash"])
    assert positions_by_isin(db_session, ledger) == expected_positions(EXPECTED["positions"])
    assert [r.sequence for r in sorted(rows, key=lambda r: r.sequence)] == list(range(1, 13))
    assert done.warnings == () and done.batches[0].to_review_skipped == 0


def test_confirm_do_not_move_cash_with_the_bond_selected(db_session, alice) -> None:
    acct = account(db_session, alice)
    expected = EXPECTED["do_not_move_cash_option"]
    p = directa(db_session, alice, acct, move_cash=False)
    chosen = frozenset({x.key for x in p.lines if x.selected} | {BOND_ORDER})
    done = confirm(db_session, alice, directa_input(acct, move_cash=False, selected=chosen))
    assert len(done.created) == expected["transactions_imported"]
    rows, ledger = ledger_of(db_session, alice, acct.id)
    assert ledger.cash == Decimal(expected["final_cash"])
    assert all(not r.affects_cash for r in rows)
    assert done.batches[0].skipped_by_choice == len(expected["skipped_by_default_seq"])
    assert done.batches[0].move_cash is False


def test_a_skipped_deposit_can_be_selected_again(db_session, alice) -> None:
    acct = account(db_session, alice)
    p = directa(db_session, alice, acct, move_cash=False)
    chosen = frozenset({x.key for x in p.lines if x.selected} | {"protocol:90000001"})
    done = confirm(db_session, alice, directa_input(acct, move_cash=False, selected=chosen))
    deposits = [r for r in done.created if r.type == "DEPOSIT"]
    assert [(r.external_ref, r.total_amount) for r in deposits] == [
        ("90000001", Decimal("5000.00"))
    ]


def test_stale_preview_is_refused(db_session, alice) -> None:
    acct = account(db_session, alice)
    seen = directa(db_session, alice, acct).fingerprint
    add_tx(db_session, acct, day=date(2024, 3, 1))  # e.g. an edit in another tab
    with pytest.raises(ApiError) as exc:
        confirm(db_session, alice, directa_input(acct), seen)
    assert (exc.value.status, exc.value.code) == (409, "IMPORT_PREVIEW_STALE")
    with pytest.raises(ApiError) as exc:
        confirm(db_session, alice, directa_input(acct), "0" * 64)
    assert exc.value.code == "IMPORT_PREVIEW_STALE"
    assert batches_of(db_session, alice) == [] and acct.external_code is None


def batches_of(db: Session, owner: models.User):
    from portfolio_tracker.repositories import imports as batch_repo

    return batch_repo.list_batches(db, owner.id)


def test_missing_choices_refuse_confirmation(db_session, alice) -> None:
    acct = account(db_session, alice)
    with pytest.raises(ApiError) as exc:
        confirm(db_session, alice, directa_input(acct, assets={}))
    assert (exc.value.status, exc.value.code) == (422, "IMPORT_NOT_READY")
    assert exc.value.extra["blocking"] == [
        {"code": "ASSET_TYPE_REQUIRED", "asset": ETF},
        {"code": "ASSET_TYPE_REQUIRED", "asset": STOCK},
    ]
    assert batches_of(db_session, alice) == []


def test_quantity_conflict_refuses_confirmation(db_session, alice) -> None:
    data = csv_file("Main,EUR,2024-01-10,BUY,IE0TEST00020,,,ETF,EUR,10,5,50,0,50,,",
                    "Main,EUR,2024-01-11,SELL,IE0TEST00020,,,ETF,EUR,11,5,55,0,55,,")  # fmt: skip
    with pytest.raises(ApiError) as exc:
        confirm(db_session, alice, ImportInput(content=data))
    assert (exc.value.status, exc.value.code) == (409, "QUANTITY_CONFLICT")
    assert exc.value.extra["conflict"]["line"] == "line:3"
    assert accounts.list_accounts(db_session, alice.id) == []


def test_csv_with_several_accounts_creates_one_batch_each(db_session, alice) -> None:
    given = UUID("00000000-0000-4000-8000-0000000000a1")
    data = csv_file(f"Main,EUR,2024-01-09,DEPOSIT,,,,,,,,,,100,,{given}",
                    "Main,EUR,2024-01-10,BUY,IE0TEST00020,EXA,Example A,ETF,EUR,10,5,50,0,50,,",
                    "Dollars,USD,2024-01-10,DEPOSIT,,,,,,,,,,70,R-9,",
                    "Dollars,USD,2024-01-10,DEPOSIT,,,,,,,,,,abc,,")  # fmt: skip
    done = confirm(db_session, alice, ImportInput(content=data, file_name="export.csv"))
    made = {a.name: a for a in accounts.list_accounts(db_session, alice.id)}
    assert {(a.name, a.currency) for a in made.values()} == {("Main", "EUR"), ("Dollars", "USD")}
    assert [(b.account_id, b.imported, b.invalid) for b in done.batches] == [
        (made["Main"].id, 2, 0), (made["Dollars"].id, 1, 1)
    ]  # fmt: skip
    rows = txs.account_ledger(db_session, alice.id, made["Main"].id)
    assert rows[0].id == given and [r.sequence for r in rows] == [1, 2]
    assert txs.account_ledger(db_session, alice.id, made["Dollars"].id)[0].sequence == 1
    assert audit_actions(db_session) == ["import.confirmed", "import.confirmed"]

    again = csv_preview(db_session, alice, data)  # the accounts are now proposed
    assert all(not a.new for a in again.accounts)
    assert [x.candidate.category for x in again.lines] == [
        Category.DUPLICATE, Category.TO_IMPORT, Category.DUPLICATE, Category.INVALID
    ]  # the row without id nor reference cannot be matched (D-104)  # fmt: skip


def test_csv_id_of_another_user_is_replaced(db_session, alice) -> None:
    bob = user(db_session, "bob")
    theirs = add_tx(db_session, account(db_session, bob))
    data = csv_file(f"Main,EUR,2024-01-05,DEPOSIT,,,,,,,,,,100,,{theirs.id}")
    done = confirm(db_session, alice, ImportInput(content=data))
    (row,) = done.created
    assert row.id != theirs.id and txs.get_transaction(db_session, bob.id, theirs.id) is not None


def test_an_account_to_create_without_selected_lines_is_not_created(db_session, alice) -> None:
    data = csv_file("Main,EUR,2024-01-09,DEPOSIT,,,,,,,,,,100,,",
                    "Spare,EUR,2024-01-09,DEPOSIT,,,,,,,,,,100,,")  # fmt: skip
    done = confirm(db_session, alice, ImportInput(content=data, selected=frozenset({"line:2"})))
    assert [a.name for a in accounts.list_accounts(db_session, alice.id)] == ["Main"]
    assert len(done.batches) == 1


# --- Final review of M3: stale previews (F1, F7, D-114) ---------------------------------------


def test_second_confirm_creating_an_account_is_stale(db_session, alice) -> None:
    """F1: a double click must not import a CSV twice into two new accounts."""
    data = csv_file("Main,EUR,2024-01-09,DEPOSIT,,,,,,,,,,100,,")
    inp = ImportInput(content=data, accounts={"Main": None})
    seen = imports.preview(db_session, alice.id, inp, today=TODAY).fingerprint
    confirm(db_session, alice, inp, seen)
    with pytest.raises(ApiError) as exc:
        confirm(db_session, alice, inp, seen)
    assert (exc.value.status, exc.value.code) == (409, "IMPORT_PREVIEW_STALE")
    assert [a.name for a in accounts.list_accounts(db_session, alice.id)] == ["Main"]


def test_second_confirm_of_a_directa_preview_is_stale(db_session, alice) -> None:
    """F7: the ticked lines are duplicates now, but the answer is "preview again"."""
    acct = account(db_session, alice)
    inp = directa_input(acct)
    seen = directa(db_session, alice, acct)
    chosen = replace_selected(inp, seen)
    confirm(db_session, alice, chosen, seen.fingerprint)
    with pytest.raises(ApiError) as exc:
        confirm(db_session, alice, chosen, seen.fingerprint)
    assert (exc.value.status, exc.value.code) == (409, "IMPORT_PREVIEW_STALE")


def replace_selected(inp: ImportInput, seen: Preview) -> ImportInput:
    from dataclasses import replace

    return replace(inp, selected=frozenset(x.key for x in seen.lines if x.selected))


def test_any_new_account_or_import_makes_a_preview_stale(db_session, alice) -> None:
    acct = account(db_session, alice)
    seen = directa(db_session, alice, acct).fingerprint
    account(db_session, alice, "Another")
    assert directa(db_session, alice, acct).fingerprint != seen
    renamed = directa(db_session, alice, acct, file_name="other.xlsx").fingerprint
    assert renamed != directa(db_session, alice, acct).fingerprint  # the name is recorded


def test_asset_columns_without_an_identifier_make_the_line_invalid(db_session, alice) -> None:
    """F6: asset type and currency but no ISIN, ticker or name: no asset, not a crash."""
    data = csv_file("Main,EUR,2024-01-10,BUY,,,,ETF,EUR,10,5,50,0,50,,")
    p = csv_preview(db_session, alice, data)
    assert p.lines[0].candidate.category is Category.INVALID and p.assets == ()
    assert [r.as_dict() for r in p.lines[0].candidate.reasons] == [
        {"field": "asset_id", "rule": "required"}
    ]


def test_a_file_bond_quoted_in_currency_is_shown_not_fixed(db_session, alice) -> None:
    """F17: the file's own type and unit are kept; a mismatch blocks until the user chooses."""
    header = "account,account_currency,trade_date,type,isin,asset_type,asset_quote_currency,"
    header += "asset_quote_unit,quantity,unit_price,gross_amount,fees,total_amount"
    data = csv_file("Main,EUR,2024-01-10,BUY,IT0TEST00031,BOND,EUR,CURRENCY,1000,97,970,0,970",
                    header=header)  # fmt: skip
    p = csv_preview(db_session, alice, data)
    assert p.assets[0].quote_unit is QuoteUnit.CURRENCY
    assert [(r.code, r.as_dict()) for r in p.blocking] == [
        ("ASSET_INVALID", {"asset": BOND, "field": "quote_unit", "rule": "must_match_asset_type"})
    ]
    fixed = csv_preview(db_session, alice, data, assets={
        BOND: AssetChoice(quote_unit=QuoteUnit.PERCENT_OF_NOMINAL)})  # fmt: skip
    assert fixed.blocking == () and fixed.lines[0].selected


# --- Final review of M3: accounts and size (F2, F3) -------------------------------------------


def test_two_file_accounts_cannot_go_to_one_account(db_session, alice) -> None:
    """F2: one batch and one dry run per account."""
    main = account(db_session, alice, "Main")
    data = csv_file("A,EUR,2024-01-09,DEPOSIT,,,,,,,,,,100,,",
                    "B,EUR,2024-01-09,DEPOSIT,,,,,,,,,,100,,")  # fmt: skip
    with pytest.raises(ApiError) as exc:
        csv_preview(db_session, alice, data, accounts={"A": main.id, "B": main.id})
    assert (exc.value.code, exc.value.extra) == ("IMPORT_CHOICE_INVALID", {"account": "B"})
    with pytest.raises(ApiError) as exc:  # a proposal and a mapping to the same account
        csv_preview(db_session, alice, data.replace(b"A,EUR", b"Main,EUR"),
                    accounts={"B": main.id})  # fmt: skip
    assert exc.value.code == "IMPORT_CHOICE_INVALID"


def test_a_file_names_at_most_1000_accounts(db_session, alice) -> None:
    """F3: per-account work is bounded (the API takes at most 1,000 account choices)."""
    rows = [f"Acc{n},EUR,2024-01-09,DEPOSIT,,,,,,,,,,1,," for n in range(1000)]
    done = confirm(db_session, alice, ImportInput(content=csv_file(*rows)))
    assert len(done.batches) == 1000 and len(done.created) == 1000
    with pytest.raises(ApiError) as exc:
        csv_preview(
            db_session, alice, csv_file(*rows, "Acc1000,EUR,2024-01-09,DEPOSIT,,,,,,,,,,1,,")
        )
    assert (exc.value.status, exc.value.code) == (413, "IMPORT_TOO_LARGE")


def test_manual_overlap_ignores_what_the_file_matches_by_id(db_session, alice) -> None:
    """F15: one's own export holds the hand-entered rows with their ids: no notice; and each
    CSV account uses the dates of its own rows."""
    main, other = account(db_session, alice, "Main"), account(db_session, alice, "Other")
    mine = add_tx(db_session, main, day=date(2024, 1, 5))  # entered by hand, no reference
    add_tx(db_session, other, day=date(2024, 6, 1))  # by hand, outside Other's rows' dates
    data = csv_file(f"Main,EUR,2024-01-05,DEPOSIT,,,,,,,,,,100,,{mine.id}",
                    "Main,EUR,2024-06-30,DEPOSIT,,,,,,,,,,100,,",
                    "Other,EUR,2024-01-02,DEPOSIT,,,,,,,,,,100,,",
                    "Other,EUR,2024-01-09,DEPOSIT,,,,,,,,,,100,,")  # fmt: skip
    assert csv_preview(db_session, alice, data).notices == ()
    add_tx(db_session, other, day=date(2024, 1, 4))
    assert [(r.code, r.as_dict()) for r in csv_preview(db_session, alice, data).notices] == [
        ("MANUAL_OVERLAP", {"account": "Other", "count": "1", "first_date": "2024-01-02",
                            "last_date": "2024-01-09"})
    ]  # fmt: skip
