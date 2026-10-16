"""Reading a Directa export into rows (docs/import/directa.md §1). Synthetic data only."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from xlsx_factory import DIRECTA_HEADER, Raw, directa_metadata, directa_row, directa_xlsx, xlsx

from portfolio_tracker.importers.base import ImportFileError
from portfolio_tracker.importers.directa_reader import read_directa

FIXTURE = Path(__file__).parents[3] / "tests/fixtures/directa/directa_synthetic_2024.xlsx"


def problems(row) -> set[tuple[str, str]]:
    return {(p.column, p.code) for p in row.problems}


def test_fixture_structure() -> None:
    file = read_directa(FIXTURE.read_bytes())
    assert file.account_code == "B0000"
    assert (file.period_from, file.period_to) == (date(2024, 1, 1), date(2024, 12, 31))
    assert len(file.rows) == 22
    assert not file.possibly_truncated
    assert all(not row.problems for row in file.rows)
    # Descending file order is kept: the reader does not reorder.
    assert [r.line for r in file.rows] == list(range(11, 33))
    first = file.rows[0]
    assert (first.movement, first.trade_date, first.value_date) == (
        "Cedola obb.", date(2024, 9, 16), date(2024, 9, 19)
    )  # fmt: skip
    assert (first.protocol, first.order_ref, first.amount) == ("90000007", None, Decimal(50))
    bond_side = [r for r in file.rows if r.order_ref == "T0000000000004"]
    assert [(r.movement, r.amount) for r in bond_side] == [
        ("Acquisto", Decimal(-4875)),
        ("Commissioni", Decimal(-5)),
        ("Ratei pass.obb.", Decimal("-12.34")),
        ("Rit.ratei pass.obb.", Decimal("1.54")),
        ("Rit.credito disaggio", Decimal("0.9")),
    ]
    assert bond_side[0].quantity == Decimal(5000)
    assert {r.currency for r in file.rows} == {"EUR"}
    assert {r.foreign_amount for r in file.rows} == {Decimal(0)}


def test_holder_name_is_never_kept() -> None:
    data = directa_xlsx(
        [directa_row("Conferimento con bonifico", 100, protocol=1)],
        account_line="Conto : B0000 Secretname Familyname",
    )
    file = read_directa(data)
    assert file.account_code == "B0000"
    assert "Secretname" not in repr(file) and "Familyname" not in repr(file)


@pytest.mark.parametrize("line", ["Conto : ", "Conto : B-00/0 Name", "Account : B0000 Name"])
def test_account_code_not_recognized(line: str) -> None:
    file = read_directa(directa_xlsx([directa_row("Conferimento con bonifico", 1)], line))
    assert file.account_code is None


def test_float_artefacts_read_as_printed() -> None:
    row = directa_row(
        "Acquisto", Raw("-1234.5699999999999"), quantity=Raw("12.300000000000001"),
        order_ref="T1",
    )  # fmt: skip
    read = read_directa(directa_xlsx([row])).rows[0]
    assert (read.amount, read.quantity, read.problems) == (Decimal("-1234.57"), Decimal("12.3"), ())


def test_amount_with_more_than_two_decimals_is_a_problem() -> None:
    row = read_directa(directa_xlsx([directa_row("Commissioni", Raw("-1.005"))])).rows[0]
    assert row.amount is None
    assert problems(row) == {("Importo euro", "AMOUNT_PRECISION")}


def test_quantity_may_have_up_to_eight_decimals() -> None:
    ok = directa_row("Acquisto", -10, quantity=Raw("0.12345678"), order_ref="T1")
    too_fine = directa_row("Acquisto", -10, quantity=Raw("0.123456789"), order_ref="T2")
    rows = read_directa(directa_xlsx([ok, too_fine])).rows
    assert rows[0].quantity == Decimal("0.12345678") and not rows[0].problems
    assert problems(rows[1]) == {("Quantità", "QUANTITY_PRECISION")}


def test_the_two_decimal_rule_is_for_the_euro_amount_only() -> None:
    row = directa_row("Acquisto", -10, foreign=Raw("1.2345"), quantity=Raw("1.5"), order_ref="T")
    read = read_directa(directa_xlsx([row])).rows[0]
    assert read.foreign_amount == Decimal("1.2345") and not read.problems


@pytest.mark.parametrize(
    ("cells", "expected"),
    [
        ({"amount": "12,50"}, {("Importo euro", "NOT_A_NUMBER")}),
        ({"amount": None}, {("Importo euro", "NOT_A_NUMBER")}),
        ({"quantity": "uno"}, {("Quantità", "NOT_A_NUMBER")}),
        ({"foreign": True}, {("Importo Divisa", "NOT_A_NUMBER")}),
        ({"day": "2024-03-15"}, {("Data operazione", "DATE_INVALID")}),
        ({"day": "31-02-2024"}, {("Data operazione", "DATE_INVALID")}),
        ({"value_day": ""}, {("Data valuta", "DATE_INVALID")}),
        ({"protocol": 1.5}, {("Protocollo", "NOT_A_CODE")}),
    ],
)
def test_cell_problems(cells: dict, expected: set) -> None:
    fields = {"amount": -1} | cells
    amount = fields.pop("amount")
    row = read_directa(directa_xlsx([directa_row("Commissioni", amount, **fields)])).rows[0]
    assert problems(row) == expected


def test_codes_are_trimmed_and_numeric_protocols_become_text() -> None:
    rows = read_directa(
        directa_xlsx(
            [
                directa_row("Acquisto", -1, order_ref="  T0000000000009  ", quantity=1),
                directa_row("Conferimento con bonifico", 1, protocol=90000001, order_ref="  "),
                directa_row("Conferimento con bonifico", 1, protocol=" 90000002 ", order_ref=""),
            ]
        )
    ).rows
    assert [(r.protocol, r.order_ref) for r in rows] == [
        (None, "T0000000000009"), ("90000001", None), ("90000002", None),
    ]  # fmt: skip


def test_empty_rows_are_skipped_but_numeric_only_rows_are_kept() -> None:
    numeric_only = [None] * 8 + [5]
    data = xlsx([*directa_metadata(), [""] * 12, None, numeric_only])
    rows = read_directa(data).rows
    assert len(rows) == 1
    assert rows[0].amount == Decimal(5) and rows[0].movement == ""
    assert ("Data operazione", "DATE_INVALID") in problems(rows[0])


def test_header_is_found_wherever_it_is() -> None:
    data = xlsx(
        [
            ["Conto : B0000 Test User"],
            list(DIRECTA_HEADER),
            directa_row("Conferimento con bonifico", 1),
        ]
    )
    file = read_directa(data)
    assert file.account_code == "B0000" and len(file.rows) == 1


@pytest.mark.parametrize(
    "header",
    [
        DIRECTA_HEADER[:-1],
        [*DIRECTA_HEADER[:7], "Quantita", *DIRECTA_HEADER[8:]],
        [*DIRECTA_HEADER, "Extra"],
    ],
)
def test_header_must_have_the_verified_columns(header: list[str]) -> None:
    data = xlsx([["Conto : B0000 Test User"], header])
    with pytest.raises(ImportFileError) as exc:
        read_directa(data)
    assert exc.value.code == "IMPORT_FILE_INVALID"


def test_no_header() -> None:
    with pytest.raises(ImportFileError) as exc:
        read_directa(xlsx([["Conto : B0000 Test User"], ["something else"]]))
    assert exc.value.code == "IMPORT_FILE_INVALID"
    assert "Test User" not in exc.value.detail


def test_header_far_down_is_not_searched() -> None:
    data = xlsx([[""]] * 60 + [list(DIRECTA_HEADER)])
    with pytest.raises(ImportFileError):
        read_directa(data)


def test_3000_rows_may_be_truncated() -> None:
    rows = [directa_row("Conferimento con bonifico", 1, protocol=i) for i in range(3000)]
    assert read_directa(directa_xlsx(rows)).possibly_truncated
    assert not read_directa(directa_xlsx(rows[:2999])).possibly_truncated
