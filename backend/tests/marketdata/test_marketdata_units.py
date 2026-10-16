"""Quote unit rules for provider data (D-44, D-119): exact rescale or refuse."""

from decimal import Decimal

import pytest

from portfolio_tracker.engine.model import AssetType, QuoteUnit
from portfolio_tracker.marketdata.units import (
    Mismatch,
    MismatchKind,
    Rescale,
    compatible,
    normalize_currency,
    to_asset_unit,
    to_stored,
)

CUR, MINOR, PCT = QuoteUnit.CURRENCY, QuoteUnit.MINOR_CURRENCY_UNIT, QuoteUnit.PERCENT_OF_NOMINAL


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("EUR", ("EUR", CUR)),
        ("usd", ("USD", CUR)),
        ("GBP", ("GBP", CUR)),
        ("GBp", ("GBP", MINOR)),
        ("GBX", ("GBP", MINOR)),
        ("gbx", ("GBP", MINOR)),
        ("ZAc", None),  # a minor code without a rule: refused, never guessed
        ("XYZ", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_provider_currency(code: str | None, expected: tuple | None) -> None:
    assert normalize_currency(code) == expected


@pytest.mark.parametrize(
    ("close", "unit", "stored"),
    [
        (Decimal("1234.5"), MINOR, Decimal("12.345")),  # pence stored as pounds
        (Decimal("0.01"), MINOR, Decimal("0.0001")),
        (Decimal("12.345"), CUR, Decimal("12.345")),
        (Decimal("98.76"), PCT, Decimal("98.76")),  # % of nominal stays as is
        (Decimal("1" * 30 + ".5"), MINOR, Decimal("1" * 28 + "." + "1" * 2 + "5")),  # > 28 digits
    ],
)
def test_to_stored_is_exact(close: Decimal, unit: QuoteUnit, stored: Decimal) -> None:
    result = to_stored(close, unit)
    assert result == stored
    assert str(result) == str(stored)  # same digits: nothing rounded


@pytest.mark.parametrize(
    ("asset_unit", "listing_unit", "factor"),
    [
        (CUR, CUR, 1),
        (CUR, MINOR, 1),  # listing in pence, asset in pounds: stored in pounds already
        (MINOR, CUR, 100),  # asset quoted in pence (D-44)
        (MINOR, MINOR, 100),
    ],
)
def test_pence_and_pounds_rescale(asset_unit: QuoteUnit, listing_unit: QuoteUnit, factor: int):
    rule = compatible("GBP", asset_unit, AssetType.STOCK, "GBP", listing_unit)
    assert rule == Rescale(Decimal(factor))
    assert to_asset_unit(Decimal("12.345"), rule) == Decimal("12.345") * factor


def test_round_trip_through_storage_keeps_every_digit() -> None:
    pence = Decimal("1234.56789")
    stored = to_stored(pence, MINOR)
    asset_rule = compatible("GBP", MINOR, AssetType.ETF, "GBP", MINOR)
    assert isinstance(asset_rule, Rescale)
    back = to_asset_unit(stored, asset_rule)
    assert back == pence and str(back) == "1234.56789"


def test_a_bond_needs_a_listing_in_percent_of_nominal() -> None:
    assert compatible("EUR", PCT, AssetType.BOND, "EUR", PCT) == Rescale(Decimal(1))
    assert compatible("EUR", PCT, AssetType.BOND, "EUR", CUR) == Mismatch(
        MismatchKind.UNIT, asset="EUR PERCENT_OF_NOMINAL", listing="EUR CURRENCY"
    )


def test_percent_listing_never_feeds_a_non_bond() -> None:
    assert compatible("EUR", CUR, AssetType.ETF, "EUR", PCT) == Mismatch(
        MismatchKind.UNIT, asset="EUR CURRENCY", listing="EUR PERCENT_OF_NOMINAL"
    )


@pytest.mark.parametrize(("asset_unit", "listing_unit"), [(CUR, CUR), (MINOR, MINOR), (PCT, PCT)])
def test_another_currency_is_refused(asset_unit: QuoteUnit, listing_unit: QuoteUnit) -> None:
    asset_type = AssetType.BOND if asset_unit is PCT else AssetType.STOCK
    result = compatible("EUR", asset_unit, asset_type, "USD", listing_unit)
    assert isinstance(result, Mismatch)
    assert result.kind is MismatchKind.CURRENCY
    assert result.asset.startswith("EUR ") and result.listing.startswith("USD ")


def test_currency_is_checked_before_unit() -> None:
    result = compatible("EUR", PCT, AssetType.BOND, "USD", CUR)
    assert isinstance(result, Mismatch) and result.kind is MismatchKind.CURRENCY
