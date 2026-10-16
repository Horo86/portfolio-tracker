"""Import service guards: the API's limits on what an importer computed, and no trace of the
file's content when it cannot be read (it may hold personal data). Invented data only."""

import base64
import logging
from datetime import date

import pytest
from sqlalchemy.orm import Session
from xlsx_factory import Raw, directa_row, directa_xlsx

from portfolio_tracker.api.errors import ApiError
from portfolio_tracker.engine.model import AssetType, TransactionType
from portfolio_tracker.importers.app_csv import AppCsvImporter
from portfolio_tracker.importers.base import MAX_BASE64_CHARS, AssetRef, Candidate, Category
from portfolio_tracker.repositories import accounts, users
from portfolio_tracker.services import imports
from portfolio_tracker.services.imports import AssetChoice, ImportInput

ETF = {"ticker": "TETF", "isin": "IE0TEST00020", "description": "TEST ETF"}
ETF_KEY = "isin:IE0TEST00020"
SECRET = "Mario Example 4242"  # stands for a holder name or an amount in a cell
TODAY = date(2026, 1, 15)


@pytest.fixture
def owner(db_session: Session):
    alice = users.add_user(db_session, username="alice", role="INVESTOR", password_hash="x",
                           language="en")  # fmt: skip
    return alice, accounts.add_account(db_session, alice.id, name="Broker", currency="EUR")


def directa(db: Session, owner, rows, choices=None) -> imports.Preview:
    alice, acct = owner
    inp = ImportInput(content=directa_xlsx(rows), account_id=acct.id,
                      assets={ETF_KEY: AssetChoice(asset_type=AssetType.ETF)}
                      if choices is None else choices)  # fmt: skip
    return imports.preview(db, alice.id, inp, today=TODAY)


def buy(amount: object, quantity: object, protocol: int = 1) -> list[object]:
    return directa_row("Acquisto", amount, quantity=quantity, order_ref="T1", protocol=protocol,
                       **ETF)  # fmt: skip


def found(p: imports.Preview) -> list[tuple[str, dict]]:
    (line,) = p.lines
    return [(r.code, r.as_dict()) for r in line.candidate.reasons]


# --- The API's limits on Directa lines (D-98) -------------------------------------------------


def test_derived_unit_price_beyond_the_limits_is_invalid(db_session, owner) -> None:
    """Each cell is within the limits; the unit price derived from them is not."""
    p = directa(db_session, owner, [buy(-10_000_000, Raw("1E-8"))])
    assert p.lines[0].candidate.category is Category.INVALID and not p.lines[0].selected
    assert found(p) == [("FIELD_INVALID", {"field": "unit_price", "problem": "OUT_OF_RANGE"})]
    assert p.blocking == () and p.selected_count == 0


def test_the_limits_are_checked_once_the_type_is_chosen(db_session, owner) -> None:
    """The unit price depends on the asset type (bonds: % of nominal)."""
    p = directa(db_session, owner, [buy(-10_000_000, Raw("1E-8"))], choices={})
    assert p.lines[0].candidate.category is Category.TO_IMPORT
    assert [r.code for r in p.blocking] == ["ASSET_TYPE_REQUIRED"]


def test_merged_executions_beyond_the_limits_are_invalid(db_session, owner) -> None:
    """Two executions of 15 integer digits each sum to 16 (D-98): refused, never rounded."""
    big = Raw("900000000000000")
    p = directa(db_session, owner, [buy(Raw("-900000000000000"), big, n) for n in (1, 2)])
    fields = {r["field"] for _, r in found(p)}
    assert fields == {"quantity", "gross_amount", "total_amount"}
    assert {r["problem"] for _, r in found(p)} == {"OUT_OF_RANGE"}


def test_text_limits_on_any_line() -> None:
    """Defence in depth: the parsers already refuse such cells."""
    line = Candidate(
        key="line:2", category=Category.TO_IMPORT, lines=(2,), type=TransactionType.DEPOSIT,
        external_ref="R" * 65, notes="n" * 2001, metadata={"k": "v" * 10_000},
        asset=AssetRef(ticker="T" * 33, name="N" * 201),
    )  # fmt: skip
    assert [r.as_dict() for r in imports._beyond_limits(line)] == [
        {"field": name, "problem": "TOO_LONG"}
        for name in ("external_ref", "notes", "ticker", "asset_name", "metadata")
    ]
    fine = Candidate(key="line:2", category=Category.TO_IMPORT, lines=(2,),
                     external_ref="R" * 64, notes="n" * 2000)  # fmt: skip
    assert imports._beyond_limits(fine) == []


# --- No trace of the file's content --------------------------------------------------------------


def no_trace(exc: BaseException, caplog: pytest.LogCaptureFixture) -> None:
    assert exc.__cause__ is None and exc.__context__ is None
    assert SECRET not in repr(exc) and SECRET not in repr(getattr(exc, "extra", {}))
    assert all(SECRET not in r.getMessage() and r.exc_info is None for r in caplog.records)


@pytest.mark.parametrize(
    "content",
    [
        f"account,trade_date,type,total_amount\n{SECRET},2024-01-10,DEPOSIT,1\n".encode()
        + b"\xff\xfe",  # not UTF-8: the decoder's error holds the whole file
        f'account,trade_date,type,total_amount\n"{SECRET}\n'.encode(),  # malformed quoting
        b"PK\x03\x04" + SECRET.encode(),  # an xlsx that is not a zip archive
    ],
)
def test_an_unreadable_file_leaves_no_trace(db_session, owner, caplog, content) -> None:
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ApiError) as exc:
        imports.preview(db_session, owner[0].id, ImportInput(content=content), today=TODAY)
    assert exc.value.code == "IMPORT_FILE_INVALID"
    no_trace(exc.value, caplog)


def test_an_unexpected_parser_failure_is_reported_by_type_only(
    db_session, owner, caplog, monkeypatch
) -> None:
    def broken(self, data, *, move_cash=True):
        raise KeyError(SECRET)

    monkeypatch.setattr(AppCsvImporter, "parse", broken)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(RuntimeError) as exc:
        imports.preview(db_session, owner[0].id, ImportInput(content=b"a,b\n"), today=TODAY)
    assert "KeyError" in str(exc.value)
    no_trace(exc.value, caplog)
    (record,) = caplog.records
    assert (record.levelname, record.exception_type) == ("ERROR", "KeyError")


def test_upload_decoding_errors_leave_no_trace(caplog) -> None:
    with pytest.raises(ApiError) as exc:
        imports.decode("!" + base64.b64encode(SECRET.encode()).decode())
    assert (exc.value.status, exc.value.code) == (422, "IMPORT_FILE_INVALID")
    no_trace(exc.value, caplog)
    with pytest.raises(ApiError) as exc:
        imports.decode("A" * (MAX_BASE64_CHARS + 4))
    assert (exc.value.status, exc.value.code) == (413, "IMPORT_TOO_LARGE")
    assert imports.decode(base64.b64encode(b"x,y\n").decode()) == b"x,y\n"
