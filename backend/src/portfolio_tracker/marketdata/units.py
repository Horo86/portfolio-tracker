"""Quote units of provider data: exact rescale or refuse (D-44, D-119).

Provider closes are stored per listing in the **major unit** of the listing currency (a close
in pence is stored in pounds), or as % of nominal for a bond listing. Several users' assets
can share one instrument with different quote units, so each asset reads the stored closes
through `compatible(...)`:

- same currency, pounds ↔ pence (`CURRENCY` ↔ `MINOR_CURRENCY_UNIT`): exact factor 1 or 100;
- a bond (`PERCENT_OF_NOMINAL`) only from a listing in % of nominal, and a % listing only
  for a bond;
- anything else is a `Mismatch` naming what differs (currency first, then unit).

Before that, a listing must fit its instrument's price class (`fits_class`, D-133).

Every rescale moves the decimal point only: no digit is ever rounded.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from portfolio_tracker.currencies import is_currency
from portfolio_tracker.engine.model import AssetType, QuoteUnit
from portfolio_tracker.marketdata.base import CLASS_UNITS, PriceClass

# Provider codes for prices quoted in a minor unit, and their major currency. Matched with
# their exact spelling first ("GBp", pence, must never be read as "GBP", pounds), then
# upper-cased. Any other non-ISO code is refused, never guessed.
MINOR_UNIT_CODES = {"GBp": "GBP", "GBX": "GBP"}
MINOR_FACTOR_EXPONENT = 2  # D-44: a minor unit is 1/100 of the major currency


class MismatchKind(StrEnum):
    CURRENCY = "CURRENCY"
    UNIT = "UNIT"


@dataclass(frozen=True, slots=True)
class Rescale:
    """Multiply a stored close by `factor` to get it in the asset's quote unit."""

    factor: Decimal


@dataclass(frozen=True, slots=True)
class Mismatch:
    """The listing cannot feed the asset. `asset` and `listing` describe each side as
    "<currency> <unit>", for the visible "price source mismatch" status."""

    kind: MismatchKind
    asset: str
    listing: str


def _shift(value: Decimal, places: int) -> Decimal:
    """Move the decimal point by `places`, exactly (no context rounding)."""
    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError(f"not a finite decimal: {value!r}")
    return Decimal((sign, digits, exponent + places))


def normalize_currency(code: str | None) -> tuple[str, QuoteUnit] | None:
    """A provider's currency code as (ISO major currency, unit); None when unknown."""
    if not code:
        return None
    for candidate in (code, code.upper()):
        if candidate in MINOR_UNIT_CODES:
            return MINOR_UNIT_CODES[candidate], QuoteUnit.MINOR_CURRENCY_UNIT
    upper = code.upper()
    if is_currency(upper):
        return upper, QuoteUnit.CURRENCY
    return None


def fits_class(price_class: PriceClass, unit: QuoteUnit) -> bool:
    """Whether a listing in `unit` may feed an instrument of `price_class` (D-133): a bond
    only in % of nominal, an ETF or a stock only in its currency or its minor unit."""
    return unit in CLASS_UNITS[price_class]


def class_of_unit(unit: QuoteUnit) -> PriceClass:
    """The price class a listing's unit implies, when no classifier answered (D-133)."""
    if unit is QuoteUnit.PERCENT_OF_NOMINAL:
        return PriceClass.BOND
    return PriceClass.ETF_STOCK


def to_stored(close: Decimal, listing_unit: QuoteUnit) -> Decimal:
    """A provider close as stored: minor units become major units (÷100, exact)."""
    if listing_unit is QuoteUnit.MINOR_CURRENCY_UNIT:
        return _shift(close, -MINOR_FACTOR_EXPONENT)
    return close


def compatible(
    asset_currency: str,
    asset_unit: QuoteUnit,
    asset_type: AssetType,
    listing_currency: str,
    listing_unit: QuoteUnit,
) -> Rescale | Mismatch:
    def mismatch(kind: MismatchKind) -> Mismatch:
        return Mismatch(
            kind, f"{asset_currency} {asset_unit.value}", f"{listing_currency} {listing_unit.value}"
        )

    if asset_currency != listing_currency:
        return mismatch(MismatchKind.CURRENCY)
    asset_is_percent = asset_type is AssetType.BOND or asset_unit is QuoteUnit.PERCENT_OF_NOMINAL
    listing_is_percent = listing_unit is QuoteUnit.PERCENT_OF_NOMINAL
    if asset_is_percent or listing_is_percent:
        if asset_is_percent and listing_is_percent and asset_unit is QuoteUnit.PERCENT_OF_NOMINAL:
            return Rescale(Decimal(1))
        return mismatch(MismatchKind.UNIT)
    if asset_unit is QuoteUnit.MINOR_CURRENCY_UNIT:
        return Rescale(Decimal(10**MINOR_FACTOR_EXPONENT))
    return Rescale(Decimal(1))


def to_asset_unit(stored: Decimal, rule: Rescale) -> Decimal:
    """A stored close in the asset's quote unit (exact)."""
    if rule.factor == 1:
        return stored
    if rule.factor == 10**MINOR_FACTOR_EXPONENT:
        return _shift(stored, MINOR_FACTOR_EXPONENT)
    raise ValueError(f"no rescale rule for factor {rule.factor}")
