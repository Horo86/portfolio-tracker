"""Fake engines and database helpers for the market data service tests: price engines and
classifiers answering from tables, invented ISINs (`ZZ`), symbols and closes."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from types import SimpleNamespace

from helpers import fake_isin
from sqlalchemy import Engine

from portfolio_tracker.db import db_transaction, models
from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.base import (
    Capabilities,
    CloseSeries,
    Identifier,
    Listing,
    PriceClass,
    ProviderError,
    Role,
    SearchQuery,
    SecurityFacts,
)
from portfolio_tracker.marketdata.registry import BuiltEngines
from portfolio_tracker.repositories import assets, instruments, users

NOW = datetime(2026, 3, 6, 18, 0, tzinfo=UTC)
RECENT = date(2026, 3, 5)
ETF_ISIN, BOND_ISIN = fake_isin(51), fake_isin(52)
EQUITY_UNITS = frozenset({QuoteUnit.CURRENCY, QuoteUnit.MINOR_CURRENCY_UNIT})
EQUITY = Capabilities(
    roles=frozenset({Role.ETF_STOCK}),
    search_isin=True,
    units=EQUITY_UNITS,
    main_exchanges=("MIL", "GER", "LSE"),
)
BONDS = Capabilities(
    roles=frozenset({Role.BOND}),
    search_isin=True,
    units=frozenset({QuoteUnit.PERCENT_OF_NOMINAL}),
)


class FakePrices:
    """A price engine answering from a table by ISIN; records every query."""

    def __init__(
        self,
        name: str,
        answers: dict[str, list[Listing] | ProviderError],
        capabilities: Capabilities = EQUITY,
        during: Callable[[], None] | None = None,
    ) -> None:
        self.name, self.capabilities, self.answers, self.during = (
            name,
            capabilities,
            answers,
            during,
        )
        self.queries: list[SearchQuery] = []
        self.closes: dict[str, CloseSeries | Exception] = {}  # by symbol
        self.fetches: list[tuple[str, date, date]] = []

    def search(self, query: SearchQuery, *, max_wait: float | None = None) -> list[Listing]:
        self.queries.append(query)
        if self.during:
            self.during()
        answer = self.answers.get(query.isin or "", [])
        if isinstance(answer, ProviderError):
            raise answer
        return list(answer)

    def daily_closes(self, symbol: str, start: date, end: date) -> CloseSeries:
        self.fetches.append((symbol, start, end))
        answer = self.closes.get(symbol)
        if answer is None:
            raise AssertionError(f"no closes set for {symbol}")
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeClassifier:
    def __init__(
        self,
        name: str,
        answers: dict[str, SecurityFacts | ProviderError | None],
        *,
        echoes_isin: bool = True,
        during: Callable[[], None] | None = None,
    ) -> None:
        self.name, self.answers, self.during = name, answers, during
        self.capabilities = Capabilities(roles=frozenset({Role.CLASSIFY}), echoes_isin=echoes_isin)
        self.asked: list[str] = []

    def classify(self, isin: str) -> SecurityFacts | None:
        self.asked.append(isin)
        if self.during:
            self.during()
        answer = self.answers.get(isin)
        if isinstance(answer, ProviderError):
            raise answer
        return answer


def built(etf=(), bond=(), classify=()) -> BuiltEngines:
    return BuiltEngines(
        {
            Role.ETF_STOCK: tuple(etf),
            Role.BOND: tuple(bond),
            Role.FX: (),
            Role.CLASSIFY: tuple(classify),
        }
    )


def listing(
    symbol: str,
    code: str | None,
    *,
    provider: str = "alpha",
    currency: str = "EUR",
    unit: QuoteUnit = QuoteUnit.CURRENCY,
    last: date | None = RECENT,
    hint: Identifier | None = None,
) -> Listing:
    return Listing(
        provider=provider,
        symbol=symbol,
        currency=currency,
        unit=unit,
        exchange=code.title() if code else None,
        exchange_code=code,
        last_close_day=last,
        hint=hint,
    )


def facts(isin: str, price_class: PriceClass | None, *hints: Identifier) -> SecurityFacts:
    return SecurityFacts(isin, price_class, tuple(hints))


def holdings(engine: Engine, *rows: tuple) -> dict[str, models.Asset]:
    """(owner, ISIN, quote currency[, asset type, ticker, name]) → that owner's asset, keyed
    "owner:ISIN:currency"."""
    made: dict[str, models.Asset] = {}
    with db_transaction(engine, write=True) as session:
        owners: dict[str, models.User] = {}
        for owner, isin, currency, *typed in rows:
            asset_type, ticker, name = (*typed, None, None, None)[:3]
            if owner not in owners:
                owners[owner] = users.add_user(
                    session, username=owner, role="INVESTOR", password_hash="x", language="en"
                )
            made[f"{owner}:{isin}:{currency}"] = assets.add_private_asset(
                session,
                owners[owner].id,
                isin=isin,
                name=name or "Example",
                asset_type=asset_type or "ETF",
                ticker=ticker,
                quote_currency=currency,
                quote_unit="CURRENCY",
            )
    return made


def stored(engine: Engine, isin: str = ETF_ISIN, currency: str = "EUR"):
    """(instrument, per engine: its check merged with its listing, if any). An engine holds
    at most one listing in these tests."""
    with db_transaction(engine, write=False) as session:
        instrument = instruments.get_instrument(session, isin, currency)
        assert instrument is not None
        found = {row.provider: row for row in instruments.listings(session, instrument.id)}
        views = {}
        for check in instruments.checks(session, instrument.id):
            listing = found.pop(check.provider, None)
            views[check.provider] = SimpleNamespace(
                status=check.status,
                mismatch=check.mismatch,
                checked_at=check.checked_at,
                failure_count=check.failure_count,
                last_error_code=check.last_error_code,
                **{name: getattr(listing, name) if listing else None for name in LISTING_FIELDS},
                is_active=bool(listing and listing.is_active),
            )
        assert found == {}  # no listing without its engine's check
    return instrument, views


LISTING_FIELDS = (
    "symbol",
    "exchange_code",
    "matched_by",
    "currency",
    "unit",
    "activated_at",
)
