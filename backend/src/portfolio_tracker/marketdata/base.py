"""What every market data provider offers and returns (D-117, ADR-0007).

A **price provider** searches listings by ISIN or ticker and returns daily closes of one
listing; an **FX provider** returns daily reference rates per EUR. Adapters are plug-ins:
each one is a module registered by name (`registry`), and nothing outside this package knows
which concrete providers exist.

Adapters normalise what they receive: a listing quoted in pence reports `currency="GBP"` with
`unit=MINOR_CURRENCY_UNIT` (D-44); prices and rates are exact decimals parsed from the
provider's text, never binary floats. Whether a listing may feed an asset is decided outside
the adapters (the unit rules, D-119).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from portfolio_tracker.engine.model import AssetType, QuoteUnit


class ErrorCode(StrEnum):
    """Stable reasons a provider call failed, recorded per listing and in job runs."""

    NETWORK = "NETWORK"  # connection refused, DNS failure, timeout
    RATE_LIMITED = "RATE_LIMITED"  # HTTP 429; `retry_after` when the provider said so
    HTTP_ERROR = "HTTP_ERROR"  # any other non-2xx answer
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    REDIRECT_REFUSED = "REDIRECT_REFUSED"  # a redirect to another host or scheme
    FORMAT_CHANGED = "FORMAT_CHANGED"  # malformed JSON or an unexpected shape
    NOT_FOUND = "NOT_FOUND"  # the provider does not know the symbol or currency
    CREDENTIAL_MISSING = "CREDENTIAL_MISSING"  # a keyed provider without its credential


class ProviderError(Exception):
    """A provider call failed. `detail` is a developer hint: it never contains credentials,
    and adapters keep response bodies out of it."""

    def __init__(
        self,
        code: ErrorCode,
        detail: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.status = status
        self.retry_after = retry_after  # seconds, from a Retry-After header


@dataclass(frozen=True, slots=True)
class Listing:
    """One provider symbol for a security. `currency` is the ISO major currency and `unit`
    says how prices are quoted (pence: GBP + MINOR_CURRENCY_UNIT; a bond in % of nominal:
    PERCENT_OF_NOMINAL). `asset_type` is the provider's proposal, None when unknown."""

    provider: str
    symbol: str
    currency: str
    unit: QuoteUnit
    isin: str | None = None
    name: str | None = None
    exchange: str | None = None
    asset_type: AssetType | None = None


@dataclass(frozen=True, slots=True)
class Close:
    """A daily close in the listing's own currency and unit."""

    day: date
    close: Decimal


@dataclass(frozen=True, slots=True)
class Rate:
    """Units of `currency` for 1 EUR on `day` (domain-rules §9: cross rates through EUR)."""

    day: date
    currency: str
    per_eur: Decimal


class PriceProvider(Protocol):
    name: str

    def search(self, query: str) -> Sequence[Listing]:
        """Listings matching an ISIN or a ticker; empty when nothing matches."""
        ...

    def daily_closes(self, symbol: str, start: date, end: date) -> Sequence[Close]:
        """Closes of one listing from `start` to `end` (both included), oldest first.
        Days without trading are simply absent."""
        ...


class FxProvider(Protocol):
    name: str

    def rates(self, currencies: Sequence[str], start: date, end: date) -> Sequence[Rate]:
        """Reference rates per EUR for `currencies` from `start` to `end` (both included).
        A currency the provider does not publish is absent from the result."""
        ...
