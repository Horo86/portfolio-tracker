import dataclasses
from datetime import date

import pytest
from factories import assets_by_id, buy, d, deposit, etf, sell, split

from portfolio_tracker.engine.edits import (
    Conflict,
    apply_changes,
    bulk_edit,
    check_ledger,
    create,
    delete,
    delete_all_for_asset,
    edit,
)
from portfolio_tracker.engine.ledger import ConflictCode
from portfolio_tracker.engine.validation import FieldIssue

JAN, FEB, MAR = date(2026, 1, 15), date(2026, 2, 16), date(2026, 3, 16)
A, B = etf(), etf()
ASSETS = assets_by_id(A, B)


def test_check_ledger_reports_field_issues_and_conflicts() -> None:
    bad = dataclasses.replace(buy(A, JAN, "1", "10"), total_amount=d("11"))
    outcome = check_ledger([bad], ASSETS)
    assert not outcome.accepted
    assert outcome.field_issues == {bad.id: (FieldIssue("total_amount", "invariant"),)}

    oversell = sell(A, JAN, "1", "10")
    outcome = check_ledger([oversell], ASSETS)
    assert outcome.conflict == Conflict(oversell.id, A.id, d("-1"), ConflictCode.NEGATIVE_QUANTITY)
    assert outcome.ledger is None


def test_create_appends_when_valid() -> None:
    first = buy(A, JAN, "1", "10")
    outcome = create([first], ASSETS, sell(A, FEB, "1", "12"))
    assert outcome.accepted
    assert outcome.ledger is not None
    assert outcome.ledger.positions[A.id].realized_gross == d("2")
    assert len(outcome.transactions) == 2


def test_rejected_change_keeps_the_original_ledger() -> None:
    first = buy(A, JAN, "1", "10")
    outcome = create([first], ASSETS, sell(A, FEB, "2", "12"))
    assert not outcome.accepted
    assert outcome.transactions == (first,)


def test_bulk_edit_is_validated_on_the_final_state_only() -> None:
    the_buy = buy(A, JAN, "10", "1000")
    the_sell = sell(A, FEB, "10", "1100")
    smaller_buy = dataclasses.replace(
        the_buy, quantity=d("5"), gross_amount=d("500"), total_amount=d("500")
    )
    smaller_sell = dataclasses.replace(
        the_sell, quantity=d("5"), gross_amount=d("550"), total_amount=d("550")
    )

    assert not edit([the_buy, the_sell], ASSETS, smaller_buy).accepted  # alone: would oversell
    outcome = bulk_edit([the_buy, the_sell], ASSETS, [smaller_buy, smaller_sell])
    assert outcome.accepted
    assert outcome.ledger is not None and outcome.ledger.positions[A.id].quantity == 0


def test_delete_all_for_asset_keeps_other_assets_and_cash_movements() -> None:
    keep = [deposit(JAN, "100"), buy(B, JAN, "1", "10")]
    outcome = delete_all_for_asset(
        [*keep, buy(A, JAN, "1", "10"), sell(A, FEB, "1", "9")], ASSETS, A.id
    )
    assert outcome.accepted
    assert outcome.transactions == tuple(keep)


def test_invalid_fields_in_an_edit_are_rejected() -> None:
    the_buy = buy(A, JAN, "1", "10")
    broken = dataclasses.replace(the_buy, fees=d("-1"))
    outcome = edit([the_buy], ASSETS, broken)
    assert not outcome.accepted
    assert the_buy.id in outcome.field_issues


def test_warnings_do_not_block_a_change() -> None:
    outcome = create([], ASSETS, buy(A, JAN, "1", "10"))  # cash goes negative: warning only
    assert outcome.accepted
    assert outcome.ledger is not None and outcome.ledger.warnings


def test_unknown_ids_and_duplicate_creates_are_programming_errors() -> None:
    the_buy = buy(A, JAN, "1", "10")
    with pytest.raises(KeyError):
        edit([], ASSETS, the_buy)
    with pytest.raises(KeyError):
        delete([], ASSETS, [the_buy.id])
    with pytest.raises(ValueError, match="already exists"):
        create([the_buy], ASSETS, the_buy)


def test_apply_changes_combines_edits_and_deletes_atomically() -> None:
    the_buy = buy(A, JAN, "10", "1000")
    the_sell = sell(A, FEB, "10", "1100")
    # Deleting the BUY while turning the SELL into a BUY is valid only as a whole.
    as_buy = buy(A, FEB, "10", "1100", id=the_sell.id, sequence=the_sell.sequence)
    outcome = apply_changes([the_buy, the_sell], ASSETS, upsert=[as_buy], delete_ids=[the_buy.id])
    assert outcome.accepted
    assert outcome.transactions == (as_buy,)


def test_an_edit_that_would_split_a_holding_to_zero_is_rejected() -> None:
    the_buy = buy(A, JAN, "1", "10")
    the_split = split(A, FEB, "1", "2")
    tiny = dataclasses.replace(the_buy, quantity=d("0.00000001"))
    outcome = edit([the_buy, the_split], ASSETS, tiny)
    assert not outcome.accepted
    assert outcome.conflict == Conflict(the_split.id, A.id, d(0), ConflictCode.SPLIT_TO_ZERO)
    assert outcome.transactions == (the_buy, the_split)


def test_duplicate_sequences_are_programming_errors() -> None:
    first = buy(A, JAN, "1", "10", sequence=500)
    with pytest.raises(ValueError, match="sequences must be unique"):
        create([first], ASSETS, buy(A, FEB, "1", "10", sequence=500))


def test_editing_and_deleting_the_same_transaction_is_a_programming_error() -> None:
    the_buy = buy(A, JAN, "1", "10")
    with pytest.raises(ValueError, match="both edited and deleted"):
        apply_changes([the_buy], ASSETS, upsert=[the_buy], delete_ids=[the_buy.id])
