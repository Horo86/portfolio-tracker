"""Amounts completed for a draft transaction (domain-rules §2, D-139).

The transaction form sends what the user typed; the preview fills in the amounts the user
did not type, so the browser never does arithmetic on money. Derived amounts are exact
(§0): a product of decimals and a division by 100 lose nothing, and nothing is rounded to
cents. An amount the user typed is never changed; validation then checks it as usual.
"""

import dataclasses
from dataclasses import dataclass

from portfolio_tracker.engine.decimals import exact
from portfolio_tracker.engine.model import Asset, QuoteUnit, Transaction, TransactionType
from portfolio_tracker.engine.validation import TRADES, expected_total

T = TransactionType

# Quote units whose price is a hundredth of the amount per unit (§2, D-44).
_PER_HUNDRED = frozenset({QuoteUnit.MINOR_CURRENCY_UNIT, QuoteUnit.PERCENT_OF_NOMINAL})


@dataclass(frozen=True, slots=True)
class Completed:
    transaction: Transaction
    derived: frozenset[str]  # the amounts filled in: "gross_amount", "total_amount"


@exact
def complete_amounts(tx: Transaction, asset: Asset | None, account_currency: str) -> Completed:
    """Fill in `gross_amount` (BUY/SELL: quantity x price in the asset's quote unit, only when
    the asset is quoted in the account currency; otherwise it is the broker's converted
    amount and must be typed) and `total_amount` (by the type's invariant, once the gross
    is known). Cash movements and splits are returned unchanged."""
    derived: set[str] = set()
    gross = tx.gross_amount
    if (
        gross is None
        and tx.type in TRADES
        and tx.quantity is not None
        and tx.unit_price is not None
        and asset is not None
        and asset.quote_currency == account_currency
    ):
        gross = tx.quantity * tx.unit_price
        if asset.quote_unit in _PER_HUNDRED:
            gross = gross / 100
        derived.add("gross_amount")
    done = dataclasses.replace(tx, gross_amount=gross)
    if done.total_amount is None and done.type is not T.STOCK_SPLIT and gross is not None:
        done = dataclasses.replace(done, total_amount=expected_total(done))
        derived.add("total_amount")
    return Completed(done, frozenset(derived))
