"""App CSV version 1 (docs/import/app-csv.md): parsing, writing, round trip, formula
protection (D-110). Synthetic data only; foreign currency is tested here (USD account, GBX
asset) because no real export in another currency exists."""

import uuid
from dataclasses import replace
from datetime import date, time
from decimal import Decimal

import pytest

from portfolio_tracker.engine.model import AssetType, QuoteUnit, SplitRatio, TransactionType
from portfolio_tracker.importers import app_csv
from portfolio_tracker.importers.app_csv import (
    COLUMNS,
    AppCsvImporter,
    CsvRecord,
    protect,
    read_csv,
    unprotect,
    write_csv,
)
from portfolio_tracker.importers.base import AccountRef, AssetRef, Category, ImportFileError
from portfolio_tracker.importers.formats import importer_for

T = TransactionType
EUR = AccountRef(name="Conto titoli", currency="EUR", provider="Directa")
USD = AccountRef(name="US account", currency="USD", provider="Broker USA")
ETF = AssetRef(isin="IE0TEST00020", ticker="TETF", name="Test ETF World",
               asset_type=AssetType.ETF, quote_currency="EUR",
               quote_unit=QuoteUnit.CURRENCY)  # fmt: skip
BTP = AssetRef(isin="IT0TEST00031", name="Test BTP 2030", asset_type=AssetType.BOND,
               quote_currency="EUR", quote_unit=QuoteUnit.PERCENT_OF_NOMINAL)  # fmt: skip
GBX = AssetRef(isin="GB0TEST00017", ticker="TLON", name="Test London plc",
               asset_type=AssetType.STOCK, quote_currency="GBP",
               quote_unit=QuoteUnit.MINOR_CURRENCY_UNIT)  # fmt: skip
D = Decimal


def rec(**fields) -> CsvRecord:
    base = {"account": EUR, "type": T.DEPOSIT, "trade_date": date(2024, 1, 10),
            "total_amount": D("100.00")}  # fmt: skip
    return CsvRecord(**(base | fields))


ALL_TYPES = [
    rec(id=uuid.UUID(int=1), value_date=date(2024, 1, 10), external_ref="90000001"),
    rec(id=uuid.UUID(int=2), type=T.BUY, asset=ETF, quantity=D("10"), unit_price=D("100.00"),
        gross_amount=D("1000.00"), fees=D("1.50"), total_amount=D("1001.50"),
        affects_cash=True, trade_time=time(9, 30), external_ref="T0000000000001"),
    rec(id=uuid.UUID(int=3), type=T.BUY, asset=BTP, quantity=D("5000"), unit_price=D("97.5"),
        gross_amount=D("4875.00"), fees=D("5.00"), accrued_interest_paid=D("12.34"),
        taxes=D("-2.44"), total_amount=D("4889.90"), affects_cash=False),
    rec(id=uuid.UUID(int=4), type=T.SELL, asset=BTP, quantity=D("2000"), unit_price=D("99"),
        gross_amount=D("1980.00"), fees=D("3.00"), accrued_interest_received=D("8.00"),
        taxes=D("3.13"), total_amount=D("1981.87"), trade_time=time(15, 4, 5, 123456)),
    rec(type=T.DIVIDEND, asset=ETF, quantity=D("19"), gross_amount=D("20.00"),
        taxes=D("5.20"), total_amount=D("14.80"), affects_cash=True),
    rec(type=T.COUPON, asset=BTP, gross_amount=D("50.00"), taxes=D("6.25"),
        total_amount=D("43.75")),
    rec(type=T.WITHDRAWAL, total_amount=D("6.20"), notes="Bollo portafoglio titoli*",
        metadata={"original_type": "Bollo portafoglio titoli*"}),
    rec(type=T.STOCK_SPLIT, asset=ETF, split_ratio=SplitRatio(new=D("1"), old=D("10")),
        notes='quotes "inside", commas, and\na new line'),
    rec(account=USD, type=T.BUY, asset=GBX, quantity=D("100"), unit_price=D("1200"),
        gross_amount=D("1500.00"), fees=D("5.00"), total_amount=D("1505.00"),
        notes="prezzo in pence · ünïcødé"),
    rec(account=USD, type=T.DEPOSIT, total_amount=D("0.00000001"), metadata={}),
]  # fmt: skip


def records(text: str) -> list[CsvRecord]:
    rows = read_csv(text.encode())
    assert all(r.issues == () for r in rows), [r.issues for r in rows]
    return [r.record for r in rows]


def issues(text: str) -> list[list[tuple[str, dict]]]:
    return [[(i.code, i.as_dict()) for i in r.issues] for r in read_csv(text.encode())]


def csv_text(*lines: str, header: str = "account,trade_date,type,total_amount") -> str:
    return "\r\n".join([header, *lines]) + "\r\n"


# --- Round trip --------------------------------------------------------------------------------


def test_round_trip_every_type() -> None:
    assert records(write_csv(ALL_TYPES)) == ALL_TYPES


def test_written_layout() -> None:
    text = write_csv(ALL_TYPES[:3])
    lines = text.split("\r\n")
    assert lines[0] == ",".join(COLUMNS)
    assert lines[0].startswith("format_version,id,account,")
    row = dict(zip(COLUMNS, lines[2].split(","), strict=True))
    assert row["format_version"] == "1"
    assert row["gross_amount"] == "1000.00"  # the stored digits, no rounding
    assert row["trade_time"] == "09:30"
    assert row["affects_cash"] == "true"
    deposit = dict(zip(COLUMNS, lines[1].split(","), strict=True))
    assert deposit["affects_cash"] == ""  # the flag exists only for trades and income
    assert text.endswith("\r\n") and not text.startswith("﻿")


def test_numbers_never_use_exponents() -> None:
    record = rec(total_amount=D("1E+2"))
    assert ",100," in write_csv([record]).split("\r\n")[1]


@pytest.mark.parametrize(
    ("value", "written"),
    [
        ("=SUM(A1)", "'=SUM(A1)"),
        ("+39 333", "'+39 333"),
        ("-note", "'-note"),
        ("@cmd", "'@cmd"),
        ("'=x", "''=x"),
        ("'hello", "'hello"),
        ("''=x", "'''=x"),
        ("'''+x", "''''+x"),
        ("'", "'"),
        ("''hello", "''hello"),
        ("plain", "plain"),
        ("a=b", "a=b"),
    ],
)
def test_formula_protection_round_trip(value: str, written: str) -> None:
    """D-110: free-text cells starting like a formula get one leading apostrophe on export
    and lose it on import; a plain leading apostrophe is written and read unchanged."""
    assert protect(value) == written
    assert unprotect(written) == value
    record = rec(notes=value, asset=replace(ETF, name=value), type=T.DIVIDEND,
                 gross_amount=D("1"), total_amount=D("1"))  # fmt: skip
    text = write_csv([record])
    row = dict(zip(COLUMNS, text.split("\r\n")[1].split(","), strict=True))
    assert row["notes"] == written and row["asset_name"] == written
    assert records(text) == [record]


def test_formula_protection_covers_every_text_column_and_never_numbers() -> None:
    """D-110 (extended at checkpoint C): account, provider, ticker, asset name, external
    reference, notes and metadata are protected; amounts and dates never are."""
    record = rec(
        account=AccountRef(name="=Main", currency="EUR", provider="+Broker"),
        type=T.BUY, asset=replace(BTP, ticker="@T", name="-Name"), quantity=D("1000"),
        gross_amount=D("970"), taxes=D("-0.40"), total_amount=D("969.60"),
        external_ref="-123", notes="=1+1", metadata={"k": "v"},
    )  # fmt: skip
    text = write_csv([record])
    row = dict(zip(COLUMNS, text.split("\r\n")[1].split(","), strict=True))
    assert [row[c] for c in ("account", "account_provider", "ticker", "asset_name",
                             "external_ref", "notes")] == [
        "'=Main", "'+Broker", "'@T", "'-Name", "'-123", "'=1+1"
    ]  # fmt: skip
    assert (row["taxes"], row["trade_date"]) == ("-0.40", "2024-01-10")
    assert records(text) == [record]


def test_hand_made_apostrophe_before_a_formula_is_removed() -> None:
    header = "account,trade_date,type,total_amount,notes"
    [record] = records(csv_text("Main,2024-01-10,DEPOSIT,5,'=x", header=header))
    assert record.notes == "=x"


# --- Hand-made files ---------------------------------------------------------------------


def test_minimal_hand_made_file_is_version_1() -> None:
    [record] = records(csv_text("Main,2024-01-10,DEPOSIT,500"))
    assert record == CsvRecord(account=AccountRef(name="Main"), type=T.DEPOSIT,
                               trade_date=date(2024, 1, 10), total_amount=D(500))  # fmt: skip


def test_byte_order_mark_is_accepted() -> None:
    rows = read_csv(b"\xef\xbb\xbf" + csv_text("Main,2024-01-10,DEPOSIT,500").encode())
    assert rows[0].record is not None


def test_empty_rows_are_skipped_and_rows_keep_their_number() -> None:
    rows = read_csv(
        csv_text("Main,2024-01-10,DEPOSIT,1", "", ",,,", "Main,2024-01-11,DEPOSIT,2").encode()
    )
    assert [r.line for r in rows] == [2, 5]


@pytest.mark.parametrize("version", ["2", "", "1.0"])
def test_unknown_format_version(version: str) -> None:
    header = "format_version,account,trade_date,type,total_amount"
    text = csv_text(f"{version},Main,2024-01-10,DEPOSIT,1", header=header)
    with pytest.raises(ImportFileError) as exc:
        read_csv(text.encode())
    assert exc.value.code == "IMPORT_FORMAT_VERSION"


def test_mixed_format_versions() -> None:
    text = csv_text("1,Main,2024-01-10,DEPOSIT,1", "2,Main,2024-01-11,DEPOSIT,1",
                    header="format_version,account,trade_date,type,total_amount")  # fmt: skip
    with pytest.raises(ImportFileError) as exc:
        read_csv(text.encode())
    assert (exc.value.code, exc.value.params) == ("IMPORT_FORMAT_VERSION", {"line": "3"})


@pytest.mark.parametrize(
    ("header", "column"),
    [
        ("account,trade_date,type,colour", "colour"),
        ("account,trade_date,type,notes,notes", "notes"),
        ("account,trade_date", "type"),
        ("Account,trade_date,type", "Account"),
    ],
)
def test_header_errors(header: str, column: str) -> None:
    with pytest.raises(ImportFileError) as exc:
        read_csv(f"{header}\r\n".encode())
    assert (exc.value.code, exc.value.params) == ("IMPORT_FILE_INVALID", {"column": column})


@pytest.mark.parametrize("data", [b"", b"\xff\xfe a,b", b'account,trade_date,type\r\n"open'])
def test_unreadable_files(data: bytes) -> None:
    with pytest.raises(ImportFileError) as exc:
        read_csv(data)
    assert exc.value.code == "IMPORT_FILE_INVALID"


def test_row_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_csv, "MAX_ROWS", 3)
    with pytest.raises(ImportFileError) as exc:
        read_csv(csv_text(*["Main,2024-01-10,DEPOSIT,1"] * 3).encode())
    assert exc.value.code == "IMPORT_TOO_LARGE"
    assert len(read_csv(csv_text(*["Main,2024-01-10,DEPOSIT,1"] * 2).encode())) == 2


# --- Row issues ----------------------------------------------------------------------------------


R = "Main,2024-01-10,DEPOSIT,1"  # a valid row of the minimal header


@pytest.mark.parametrize(
    ("header", "row", "field", "problem"),
    [
        ("", ",2024-01-10,DEPOSIT,1", "account", "MISSING"),
        ("", "Main,,DEPOSIT,1", "trade_date", "MISSING"),
        ("", "Main,10/01/2024,DEPOSIT,1", "trade_date", "NOT_A_DATE"),
        ("", "Main,2024-02-30,DEPOSIT,1", "trade_date", "NOT_A_DATE"),
        ("", "Main,2024-01-10,deposit,1", "type", "UNKNOWN_TYPE"),
        ("", "Main,2024-01-10,DEPOSIT,\"1,5\"", "total_amount", "NOT_A_DECIMAL"),
        ("", "Main,2024-01-10,DEPOSIT,1e3", "total_amount", "NOT_A_DECIMAL"),
        ("", "Main,2024-01-10,DEPOSIT,1234567890123456", "total_amount", "NOT_A_DECIMAL"),
        (",trade_time", "Main,2024-01-10,DEPOSIT,1,9.30", "trade_time", "NOT_A_TIME"),
        (",id", "Main,2024-01-10,DEPOSIT,1,not-a-uuid", "id", "NOT_A_UUID"),
        (",isin", "Main,2024-01-10,DEPOSIT,1,IE0TEST00021", "isin", "NOT_AN_ISIN"),
        (",account_currency", R + ",EURO", "account_currency", "NOT_A_CURRENCY"),
        (",asset_type", "Main,2024-01-10,DEPOSIT,1,FUND", "asset_type", "UNKNOWN_ASSET_TYPE"),
        (",asset_quote_unit", R + ",GBX", "asset_quote_unit", "UNKNOWN_QUOTE_UNIT"),
        (",split_ratio", "Main,2024-01-10,DEPOSIT,1,2/1", "split_ratio", "NOT_A_SPLIT_RATIO"),
        (",affects_cash", "Main,2024-01-10,DEPOSIT,1,yes", "affects_cash", "NOT_A_BOOLEAN"),
        (",metadata", "Main,2024-01-10,DEPOSIT,1,[1]", "metadata", "NOT_A_JSON_OBJECT"),
        (",metadata", "Main,2024-01-10,DEPOSIT,1,{oops", "metadata", "NOT_A_JSON_OBJECT"),
        (",external_ref", "Main,2024-01-10,DEPOSIT,1," + "x" * 65, "external_ref", "TOO_LONG"),
        (",notes", "Main,2024-01-10,DEPOSIT,1," + "n" * 2001, "notes", "TOO_LONG"),
    ],
)  # fmt: skip
def test_field_issues(header: str, row: str, field: str, problem: str) -> None:
    text = csv_text(row, header="account,trade_date,type,total_amount" + header)
    assert issues(text) == [[("FIELD_INVALID", {"field": field, "problem": problem})]]


def test_isin_is_upper_cased_and_checked() -> None:
    header = "account,trade_date,type,total_amount,isin"
    text = csv_text("Main,2024-01-10,DIVIDEND,1,ie0test00020", header=header)
    assert records(text)[0].asset == AssetRef(isin="IE0TEST00020")


def test_wrong_field_count() -> None:
    assert issues(csv_text("Main,2024-01-10,DEPOSIT,1,extra")) == [
        [("FIELD_COUNT", {"expected": "4", "found": "5"})]
    ]


def test_duplicate_id_in_one_file() -> None:
    same = "00000000-0000-0000-0000-000000000001"
    text = csv_text(f"Main,2024-01-10,DEPOSIT,1,{same}", f"Main,2024-01-11,DEPOSIT,2,{same}",
                    "Main,2024-01-12,DEPOSIT,3,",
                    header="account,trade_date,type,total_amount,id")  # fmt: skip
    assert issues(text) == [
        [("DUPLICATE_ID", {"lines": "3"})], [("DUPLICATE_ID", {"lines": "2"})], []
    ]  # fmt: skip


def test_all_issues_of_a_row_are_reported() -> None:
    assert issues(csv_text(",someday,XYZ,abc")) == [[
        ("FIELD_INVALID", {"field": "account", "problem": "MISSING"}),
        ("FIELD_INVALID", {"field": "trade_date", "problem": "NOT_A_DATE"}),
        ("FIELD_INVALID", {"field": "type", "problem": "UNKNOWN_TYPE"}),
        ("FIELD_INVALID", {"field": "total_amount", "problem": "NOT_A_DECIMAL"}),
    ]]  # fmt: skip


# --- Importer -------------------------------------------------------------------------------------


def test_importer_candidates() -> None:
    text = write_csv(ALL_TYPES[:2]) + "Main,bad\r\n"
    parsed = AppCsvImporter().parse(text.encode())
    assert parsed.source == "app-csv"
    first, second, bad = parsed.candidates
    assert (first.category, first.sequence, first.lines, first.id) == (
        Category.TO_IMPORT, 1, (2,), uuid.UUID(int=1)
    )  # fmt: skip
    assert (second.type, second.asset, second.fees, second.trade_time) == (
        T.BUY, ETF, D("1.50"), time(9, 30)
    )  # fmt: skip
    assert second.account == EUR
    assert (bad.category, bad.sequence, bad.lines) == (Category.INVALID, None, (4,))
    assert (parsed.first_date, parsed.last_date) == (date(2024, 1, 10), date(2024, 1, 10))


def test_importer_without_cash() -> None:
    parsed = AppCsvImporter().parse(write_csv(ALL_TYPES).encode(), move_cash=False)
    by_type = {}
    for c in parsed.candidates:
        by_type.setdefault(c.type, []).append(c)
    assert all(
        not c.affects_cash for t in (T.BUY, T.SELL, T.DIVIDEND, T.COUPON) for c in by_type[t]
    )
    assert {c.category for t in (T.DEPOSIT, T.WITHDRAWAL) for c in by_type[t]} == {
        Category.SKIPPED_BY_CHOICE
    }
    assert by_type[T.STOCK_SPLIT][0].category is Category.TO_IMPORT


def test_format_detection() -> None:
    from xlsx_factory import directa_row, directa_xlsx

    xlsx = directa_xlsx([directa_row("Conferimento con bonifico", 1, protocol=1)])
    assert importer_for(xlsx).source == "directa-xlsx"
    assert importer_for(b"account,trade_date,type\r\n").source == "app-csv"
    assert importer_for(xlsx, "app-csv").source == "app-csv"
    with pytest.raises(ImportFileError) as exc:
        importer_for(b"", "fineco-xlsx")
    assert exc.value.code == "IMPORT_FORMAT_UNKNOWN"


CASH_ROWS = csv_text(
    "Main,2024-01-10,BUY,10,true",
    "Main,2024-01-11,BUY,10,false",
    "Main,2024-01-12,BUY,10,",
    "Main,2024-01-13,DEPOSIT,10,",
    header="account,trade_date,type,total_amount,affects_cash",
)


def test_option_off_keeps_each_rows_own_affects_cash() -> None:
    """D-112: without the option the file decides, row by row (empty = true)."""
    parsed = AppCsvImporter().parse(CASH_ROWS.encode(), move_cash=True)
    assert [(c.type, c.affects_cash, c.category) for c in parsed.candidates] == [
        (T.BUY, True, Category.TO_IMPORT),
        (T.BUY, False, Category.TO_IMPORT),
        (T.BUY, True, Category.TO_IMPORT),
        (T.DEPOSIT, True, Category.TO_IMPORT),
    ]


def test_option_on_imports_every_trade_without_moving_cash() -> None:
    """D-112: with the option every trade/income row is false, even one marked true;
    deposits are skipped by default instead (they have no flag)."""
    parsed = AppCsvImporter().parse(CASH_ROWS.encode(), move_cash=False)
    assert [(c.type, c.affects_cash, c.category) for c in parsed.candidates] == [
        (T.BUY, False, Category.TO_IMPORT),
        (T.BUY, False, Category.TO_IMPORT),
        (T.BUY, False, Category.TO_IMPORT),
        (T.DEPOSIT, True, Category.SKIPPED_BY_CHOICE),
    ]


# --- Fixes after the Opus review of M3 (findings 6, 7, 11, 12) ---------------------------------


@pytest.mark.parametrize("value", ["", "'", "''", "=", "'=", "''=", "' =x", "'\t=x"])
def test_protection_reverses_exactly(value: str) -> None:
    """Finding 7: protect then unprotect is the identity, whatever the leading apostrophes."""
    assert unprotect(protect(value)) == value


def test_duplicate_id_is_found_when_the_other_row_is_invalid() -> None:
    """Finding 6: fixing the invalid row later must not make it a silent duplicate."""
    same = "00000000-0000-0000-0000-00000000000a"
    text = csv_text(f"Main,2024-01-10,DEPOSIT,1,{same}",
                    f"Main,2024-01-11,DEPOSIT,abc,{same.upper()}",
                    header="account,trade_date,type,total_amount,id")  # fmt: skip
    assert issues(text) == [
        [("DUPLICATE_ID", {"lines": "3"})],
        [("FIELD_INVALID", {"field": "total_amount", "problem": "NOT_A_DECIMAL"}),
         ("DUPLICATE_ID", {"lines": "2"})],
    ]  # fmt: skip


@pytest.mark.parametrize("tx_type", ["DEPOSIT", "WITHDRAWAL", "STOCK_SPLIT"])
@pytest.mark.parametrize("flag", ["true", "false"])
def test_affects_cash_is_refused_where_the_type_has_no_such_flag(tx_type: str, flag: str) -> None:
    """Finding 11: never a selectable row that the engine would refuse at confirmation."""
    text = csv_text(f"Main,2024-01-10,{tx_type},1,{flag}",
                    header="account,trade_date,type,total_amount,affects_cash")  # fmt: skip
    assert issues(text) == [
        [("FIELD_INVALID", {"field": "affects_cash", "problem": "NOT_ALLOWED_FOR_TYPE"})]
    ]


@pytest.mark.parametrize("payload", ['{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}'])
def test_metadata_refuses_non_json_constants(payload: str) -> None:
    """Finding 12: they would be written back as invalid JSON."""
    quoted = '"' + payload.replace('"', '""') + '"'  # CSV doubles quotes inside a cell
    text = csv_text(f"Main,2024-01-10,DEPOSIT,1,{quoted}",
                    header="account,trade_date,type,total_amount,metadata")  # fmt: skip
    assert issues(text) == [
        [("FIELD_INVALID", {"field": "metadata", "problem": "NOT_A_JSON_OBJECT"})]
    ]


# --- Final review of M3: one metadata measure (F9, D-114) -------------------------------------


def test_metadata_is_measured_as_its_canonical_text() -> None:
    """Accents count once (not as \\u escapes); spaces of a hand-made cell do not count."""
    accented = '{"k": "' + "è" * 9_000 + '"}'
    spaced = '{ "k" :  "' + "v" * 9_000 + '" ' + " " * 3_000 + "}"
    text = csv_text(f'Main,2024-01-10,DEPOSIT,1,"{accented.replace(chr(34), chr(34) * 2)}"',
                    f'Main,2024-01-10,DEPOSIT,1,"{spaced.replace(chr(34), chr(34) * 2)}"',
                    header="account,trade_date,type,total_amount,metadata")  # fmt: skip
    assert issues(text) == [[], []]
    over = '{"k": "' + "v" * 10_000 + '"}'
    text = csv_text(f'Main,2024-01-10,DEPOSIT,1,"{over.replace(chr(34), chr(34) * 2)}"',
                    header="account,trade_date,type,total_amount,metadata")  # fmt: skip
    assert issues(text) == [[("FIELD_INVALID", {"field": "metadata", "problem": "TOO_LONG"})]]
