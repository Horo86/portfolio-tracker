"""What every market data engine offers and returns (D-117, D-131, ADR-0007).

An **engine** is one provider adapter. It declares its `Capabilities`, among them the
**roles** it can serve: a price role (`ETF_STOCK`, `BOND`) searches listings and returns daily
closes of one listing; `FX` returns daily reference rates per EUR; `CLASSIFY` tells the price
class of an ISIN. Engines are plug-ins: each one is a module registered by name
(`registry`), and nothing outside this package knows which concrete engines exist.

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


class PriceClass(StrEnum):
    """What kind of security an instrument is, for routing (D-133): each class has its own
    order of engines. A fact about the security, learned from engines, never from a user."""

    ETF_STOCK = "ETF_STOCK"
    BOND = "BOND"


class Role(StrEnum):
    """What an engine can be ordered for (D-131, D-132)."""

    ETF_STOCK = "ETF_STOCK"  # daily closes of ETFs and stocks
    BOND = "BOND"  # daily closes of bonds, in % of nominal
    FX = "FX"  # daily rates per EUR
    CLASSIFY = "CLASSIFY"  # the price class (and identifiers) of an ISIN


PRICE_ROLES = {PriceClass.ETF_STOCK: Role.ETF_STOCK, PriceClass.BOND: Role.BOND}

# The units a listing of each price class may be quoted in (D-133).
CLASS_UNITS = {
    PriceClass.ETF_STOCK: frozenset({QuoteUnit.CURRENCY, QuoteUnit.MINOR_CURRENCY_UNIT}),
    PriceClass.BOND: frozenset({QuoteUnit.PERCENT_OF_NOMINAL}),
}


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What an engine declares about itself (D-131). Only verified facts are declared: a
    unit left out makes assets keep their manual prices instead of being valued 100x off."""

    roles: frozenset[Role]
    search_isin: bool = False  # search accepts an ISIN (with optional hints)
    search_text: bool = False  # search accepts a ticker or a name (the asset search box)
    echoes_isin: bool = False  # its answers carry the ISIN back (a confirmed match)
    units: frozenset[QuoteUnit] = frozenset()  # units its listings may report
    history_years: int | None = None  # how far back it goes; None: its earliest date
    needs_credential: bool = False
    credential_label: str | None = None  # label key for the admin form (M6)
    official: bool = True  # False: an unofficial API that may break without notice
    terms_url: str | None = None

    def __post_init__(self) -> None:
        if not self.roles:
            raise ValueError("an engine serves at least one role")
        for price_class, role in PRICE_ROLES.items():
            if role in self.roles:
                if not self.search_isin:
                    raise ValueError(f"a {role} engine must search by ISIN")
                if not self.units & CLASS_UNITS[price_class]:
                    raise ValueError(f"a {role} engine must declare a unit of that class")
        if self.units and not self.roles & set(PRICE_ROLES.values()):
            raise ValueError("units are declared by price engines only")
        if self.credential_label and not self.needs_credential:
            raise ValueError("a credential label needs a keyed engine")
        if self.history_years is not None and self.history_years < 1:
            raise ValueError("history_years must be at least 1")


@dataclass(frozen=True, slots=True)
class Identifier:
    """A ticker on an exchange, as a classifier reports it (e.g. an ISO 10383 MIC). Each price
    engine maps identifiers to its own symbols inside its adapter."""

    ticker: str
    exchange: str | None = None


@dataclass(frozen=True, slots=True)
class SecurityFacts:
    """What a classifier knows about an ISIN. `identifiers` may anchor another engine's
    match only when the classifier carries the ISIN back (`echoes_isin`, D-133)."""

    isin: str
    price_class: PriceClass | None
    identifiers: tuple[Identifier, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A search by ISIN (linking an instrument, with optional `hints` from a classifier) or
    by text (the asset search box). Exactly one of `isin` and `text` is set."""

    isin: str | None = None
    text: str | None = None
    hints: tuple[Identifier, ...] = ()

    def __post_init__(self) -> None:
        if (self.isin is None) == (self.text is None):
            raise ValueError("a search has either an ISIN or a text")
        if self.hints and self.isin is None:
            raise ValueError("hints only go with an ISIN search")

    @classmethod
    def by_isin(cls, isin: str, hints: Sequence[Identifier] = ()) -> "SearchQuery":
        return cls(isin=isin, hints=tuple(hints))

    @classmethod
    def by_text(cls, text: str) -> "SearchQuery":
        return cls(text=text)

    @property
    def value(self) -> str:
        """The ISIN or the text, as sent to the provider."""
        return self.isin if self.isin is not None else self.text or ""


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
class CloseSeries:
    """Closes of one listing with the currency and unit the provider reported *for this
    answer*: the caller checks them against the stored listing, so a provider that changes a
    listing's unit (e.g. pounds to pence) is caught instead of stored 100x off (D-119)."""

    currency: str
    unit: QuoteUnit
    closes: tuple[Close, ...] = ()


@dataclass(frozen=True, slots=True)
class Rate:
    """Units of `currency` for 1 EUR on `day` (domain-rules §9: cross rates through EUR)."""

    day: date
    currency: str
    per_eur: Decimal


class PriceProvider(Protocol):
    name: str
    capabilities: Capabilities

    def search(self, query: SearchQuery, *, max_wait: float | None = None) -> Sequence[Listing]:
        """Listings matching the query; empty when nothing matches. `max_wait` bounds the wait
        for the throttle (a search request cannot wait long)."""
        ...

    def daily_closes(self, symbol: str, start: date, end: date) -> CloseSeries:
        """Closes of one listing from `start` to `end` (both included), oldest first.
        Days without trading are simply absent."""
        ...


class FxProvider(Protocol):
    name: str
    capabilities: Capabilities

    def rates(self, currencies: Sequence[str], start: date, end: date) -> Sequence[Rate]:
        """Reference rates per EUR for `currencies` from `start` to `end` (both included).
        A currency the provider does not publish is absent from the result."""
        ...


class Classifier(Protocol):
    name: str
    capabilities: Capabilities

    def classify(self, isin: str) -> SecurityFacts | None:
        """The price class and identifiers of `isin`; None when the engine does not know it."""
        ...
