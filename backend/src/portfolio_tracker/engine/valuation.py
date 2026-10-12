"""Valuation of an account on a date from in-memory prices and FX rates (D-55).

Rules: domain-rules §2 quote units, §5 unrealized, §9 FX, §12 missing data (D-46),
one rounding on a converted amount (D-58). Providers and stored history are M4.
"""

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from portfolio_tracker.engine.decimals import check_decimal, engine_context, exact, q8, ratio8
from portfolio_tracker.engine.ledger import Ledger, Position
from portfolio_tracker.engine.metrics import AccountTotals, summarize
from portfolio_tracker.engine.model import ZERO, Asset, QuoteUnit

FX_BASE = "EUR"  # ECB reference rates: units of a currency per 1 EUR

_QUOTE_DIVISOR = {
    QuoteUnit.CURRENCY: Decimal(1),
    QuoteUnit.MINOR_CURRENCY_UNIT: Decimal(100),
    QuoteUnit.PERCENT_OF_NOMINAL: Decimal(100),
}


@exact
def quote_amount(asset: Asset, quantity: Decimal, price: Decimal) -> Decimal:
    """quantity x price in the asset's quote currency, applying the quote unit (§2, D-44).

    Exact (not rounded): valuation rounds it once; the UI can offer it as `gross_amount`
    when the asset is quoted in the account currency.
    """
    return quantity * price / _QUOTE_DIVISOR[asset.quote_unit]


class _DatedSeries:
    """Values by date with carry-forward of the last value on or before a day."""

    def __init__(self, values: Mapping[date, Decimal]) -> None:
        for day, value in values.items():
            check_decimal(f"value on {day}", value)
        self._dates = sorted(values)
        self._values = [values[day] for day in self._dates]

    def on_or_before(self, day: date) -> tuple[date, Decimal] | None:
        index = bisect_right(self._dates, day)
        if index == 0:
            return None
        return self._dates[index - 1], self._values[index - 1]


class PriceHistory(_DatedSeries):
    """Daily closes of one asset, in its quote unit (already merged with manual prices)."""


class FxRates:
    def __init__(self, rates: Mapping[str, Mapping[date, Decimal]]) -> None:
        self._series = {currency: _DatedSeries(values) for currency, values in rates.items()}

    def rate(self, currency: str, day: date) -> Decimal | None:
        """Units of `currency` per 1 EUR, last available on or before `day`."""
        if currency == FX_BASE:
            return Decimal(1)
        series = self._series.get(currency)
        found = series.on_or_before(day) if series else None
        return found[1] if found else None

    def convert(self, amount: Decimal, source: str, target: str, day: date) -> Decimal | None:
        """Convert through EUR, rounded once; None when a rate is missing (never invented)."""
        if source == target:
            return amount
        source_rate, target_rate = self.rate(source, day), self.rate(target, day)
        if source_rate is None or target_rate is None:
            return None
        with engine_context():
            return q8(amount * target_rate / source_rate)


@dataclass(frozen=True, slots=True)
class PositionValue:
    asset_id: UUID
    cost_basis: Decimal
    value: Decimal | None  # account currency; None when FX is missing
    estimated_at_cost: bool  # no price on or before the day (D-46)
    price_date: date | None

    @property
    @exact
    def unrealized(self) -> Decimal | None:
        return None if self.value is None else self.value - self.cost_basis

    @property
    @exact
    def unrealized_pct(self) -> Decimal | None:
        unrealized = self.unrealized
        return None if unrealized is None else ratio8(unrealized, self.cost_basis)


@dataclass(frozen=True, slots=True)
class AccountValuation:
    """Account figures in the account currency on one day (D-36)."""

    day: date
    positions: dict[UUID, PositionValue]
    cash: Decimal
    totals: AccountTotals
    partial: bool  # some position could not be converted (missing FX)

    @property
    @exact
    def securities_value(self) -> Decimal:
        return sum((p.value for p in self._valued()), ZERO)

    @property
    @exact
    def total_value(self) -> Decimal:
        return self.securities_value + self.cash

    @property
    @exact
    def unrealized(self) -> Decimal:
        return self.securities_value - sum((p.cost_basis for p in self._valued()), ZERO)

    @property
    @exact
    def unrealized_pct(self) -> Decimal | None:
        return ratio8(self.unrealized, sum((p.cost_basis for p in self._valued()), ZERO))

    @property
    @exact
    def gain_gross(self) -> Decimal:
        return self.unrealized + self.totals.realized_gross + self.totals.income_gross

    @property
    @exact
    def gain_net(self) -> Decimal:
        return self.gain_gross - self.totals.taxes

    def _valued(self) -> list[PositionValue]:
        return [p for p in self.positions.values() if p.value is not None]


def value_account(
    ledger: Ledger,
    assets: Mapping[UUID, Asset],
    prices: Mapping[UUID, PriceHistory],
    fx: FxRates,
    account_currency: str,
    day: date,
) -> AccountValuation:
    """Value `ledger` (replayed up to `day`) with the prices and rates known on `day`.

    The ledger must hold exactly the transactions up to `day`: `replay(txs, as_of=day)`, or a
    full replay whose last transaction is not after `day`. Anything else raises ValueError.
    """
    too_late = ledger.last_date is not None and ledger.last_date > day
    too_early = ledger.as_of is not None and ledger.as_of < day
    if too_late or too_early:
        raise ValueError(
            f"ledger replayed to {ledger.as_of or ledger.last_date} cannot be valued on {day}"
        )
    values = {
        asset_id: _value_position(
            pos, assets[asset_id], prices.get(asset_id), fx, account_currency, day
        )
        for asset_id, pos in ledger.positions.items()
    }
    return AccountValuation(
        day=day,
        positions=values,
        cash=ledger.cash,
        totals=summarize(ledger),
        partial=any(v.value is None for v in values.values()),
    )


def _value_position(
    pos: Position,
    asset: Asset,
    prices: PriceHistory | None,
    fx: FxRates,
    account_currency: str,
    day: date,
) -> PositionValue:
    if pos.quantity == 0:
        return PositionValue(pos.asset_id, pos.cost_basis, ZERO, False, None)
    found = prices.on_or_before(day) if prices else None
    if found is None:
        return PositionValue(pos.asset_id, pos.cost_basis, pos.cost_basis, True, None)
    price_date, price = found
    in_quote_currency = quote_amount(asset, pos.quantity, price)
    if asset.quote_currency == account_currency:
        value: Decimal | None = q8(in_quote_currency)  # rounded once, like a conversion (D-58)
    else:
        value = fx.convert(in_quote_currency, asset.quote_currency, account_currency, day)
    return PositionValue(pos.asset_id, pos.cost_basis, value, False, price_date)
