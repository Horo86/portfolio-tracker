"""Market data service: linking assets to instruments, classifying instruments and choosing
their listings (D-116, D-121, D-125, D-133, D-134, D-136).

Runs inside the queue's jobs only, never inside a request (D-125: no response may tell a user
whether another user holds an ISIN). Everything here is internal: nothing is returned to a
user, and logs and results carry engine names, error codes and counts, never an ISIN.

**Linking** gives every asset with an ISIN the instrument of its (ISIN, quote currency):
local work in one short write transaction.

**Choosing a listing** for an instrument without an active listing (network):
1. *Class* (D-133): the classifiers are asked in order; the first class answered for that ISIN
   is kept with the engine's name. Only a classifier that carries the ISIN back
   (`echoes_isin`) passes its identifiers to the price engines as hints (D-136). When a
   classifier fails with an error and none answered a class, the instrument waits for the
   next run: a class inferred from a unit meanwhile could send a bond to an equity engine.
2. *Order*: the engines of the instrument's class; without a class, the `BOND` order first,
   then `ETF_STOCK`, and the class is then inferred from the accepted listing's unit (`UNIT`).
3. *Listing* (D-125, D-136): each engine is asked by ISIN in turn; its candidates are put in
   the fixed rank (`ranking.py`) and the first one `acceptance.rejection` accepts is the
   listing, made active at once (no other listing exists). An engine in both orders is asked
   once and its answer judged under each class. The first engine with an accepted
   listing ends the walk: later engines are not asked.
4. *What is recorded per engine asked*: `OK` with the listing; `NOT_FOUND` when nothing was
   found or nothing had a recent close; `MISMATCH` (`CURRENCY`, or `UNIT` for a unit that the
   engine does not declare or that does not fit the class) when candidates exist but none
   fits, judged on the best-ranked one; `FAILING` with the error code on a provider error,
   when the next engine is tried in the same run. `NOT_FOUND` and `MISMATCH` are asked again
   after `RETRY_DAYS`; `FAILING` on the next run.

Network discipline (D-121): what to do is read in a short read transaction, engines are
called with no transaction open, and each instrument's outcome is written in its own short
write transaction, re-read there so that a concurrent change is never overwritten blindly.
"""

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Engine

from portfolio_tracker.db import db_transaction
from portfolio_tracker.db.models import Instrument
from portfolio_tracker.marketdata.acceptance import Rejection, rejection
from portfolio_tracker.marketdata.base import (
    Identifier,
    Listing,
    PriceClass,
    PriceProvider,
    ProviderError,
    SearchQuery,
)
from portfolio_tracker.marketdata.ranking import candidate_rank
from portfolio_tracker.marketdata.registry import BuiltEngines
from portfolio_tracker.marketdata.units import MismatchKind
from portfolio_tracker.repositories import instruments

log = logging.getLogger(__name__)

# A listing an engine did not have (NOT_FOUND, or only unfit candidates: MISMATCH) is
# searched again after this many days (D-134).
RETRY_DAYS = 30
# Unknown class: the bond order first, then the ETF/stock one (D-133).
UNKNOWN_CLASS_ORDER = (PriceClass.BOND, PriceClass.ETF_STOCK)
_RETRIED = frozenset({"NOT_FOUND", "MISMATCH"})
_MISMATCH = {
    Rejection.CURRENCY: MismatchKind.CURRENCY,
    Rejection.UNIT: MismatchKind.UNIT,
    Rejection.CLASS: MismatchKind.UNIT,
}


@dataclass
class ResolveResult:
    """Counts only (they go to the job log, D-121)."""

    linked: int = 0  # assets linked to an instrument
    created: int = 0  # instruments created for them
    chosen: int = 0  # instruments that got their listing
    not_found: int = 0  # every engine asked answered, none with an accepted listing
    postponed: int = 0  # a classifier failed and no class was known
    no_engine: int = 0  # nothing to ask (no engine for the class, or all waiting to retry)
    classified: Counter[str] = field(default_factory=Counter)  # by source: engine or UNIT
    errors: Counter[str] = field(default_factory=Counter)  # by error code


@dataclass(frozen=True)
class _Known:
    """What is stored about one engine's listing for an instrument."""

    status: str
    checked_at: datetime | None


@dataclass(frozen=True)
class _Pending:
    """An instrument without an active listing, as read before the network calls."""

    id: UUID
    isin: str
    currency: str
    price_class: PriceClass | None
    known: dict[str, _Known]


@dataclass(frozen=True)
class _Outcome:
    """One engine's answer for an instrument."""

    provider: str
    status: str
    listing: Listing | None = None
    mismatch: MismatchKind | None = None
    error: str | None = None


def link_assets(engine: Engine) -> tuple[int, int]:
    """Link every asset with an ISIN that has no instrument yet (any owner). Returns the
    number of assets linked and of instruments created."""
    linked = created = 0
    with db_transaction(engine, write=True) as session:
        for asset in instruments.assets_to_link(session):
            assert asset.isin is not None  # assets_to_link selects assets with an ISIN
            if instruments.get_instrument(session, asset.isin, asset.quote_currency) is None:
                created += 1
            instrument = instruments.get_or_create_instrument(
                session, asset.isin, asset.quote_currency
            )
            instruments.link_asset(session, asset.id, instrument.id)
            linked += 1
    return linked, created


def choose_listings(
    engine: Engine, engines: BuiltEngines, exchanges: Sequence[str], now: datetime
) -> ResolveResult:
    """Link new assets, then give every linked instrument without an active listing the
    listing of the first engine (in its class's order) with an accepted candidate."""
    result = ResolveResult()
    result.linked, result.created = link_assets(engine)
    with db_transaction(engine, write=False) as session:
        pending = [
            _Pending(
                instrument.id,
                instrument.isin,
                instrument.quote_currency,
                PriceClass(instrument.price_class) if instrument.price_class else None,
                {
                    row.provider: _Known(row.status, row.checked_at)
                    for row in instruments.listings(session, instrument.id)
                },
            )
            for instrument in instruments.linked_instruments(session)
            if instruments.active_listing(session, instrument.id) is None
        ]
    for item in pending:
        _choose(engine, engines, exchanges, now, item, result)
    return result


def _due(known: _Known | None, now: datetime) -> bool:
    """Whether an engine is to be asked (again) for an instrument."""
    if known is None or known.status not in _RETRIED:
        return True  # never asked, failing, or found earlier but not active
    return known.checked_at is None or known.checked_at <= now - timedelta(days=RETRY_DAYS)


def _orders(
    engines: BuiltEngines, price_class: PriceClass | None
) -> list[tuple[PriceClass, PriceProvider]]:
    classes = (price_class,) if price_class else UNKNOWN_CLASS_ORDER
    return [(c, provider) for c in classes for provider in engines.prices(c)]


def _classify(
    engines: BuiltEngines, isin: str, result: ResolveResult
) -> tuple[PriceClass | None, str | None, tuple[Identifier, ...], bool]:
    """(class, source engine, hints, whether a classifier failed)."""
    price_class: PriceClass | None = None
    source: str | None = None
    hints: tuple[Identifier, ...] = ()
    failed = False
    for classifier in engines.classifiers():
        try:
            facts = classifier.classify(isin)
        except ProviderError as error:
            failed = True
            result.errors[error.code] += 1
            log.warning(
                "classifier failed", extra={"provider": classifier.name, "code": error.code}
            )
            continue
        if facts is None:
            continue
        if not hints and classifier.capabilities.echoes_isin:
            hints = facts.identifiers  # only an exact ISIN lookup may anchor a match (D-133)
        if facts.price_class is not None:
            price_class, source = facts.price_class, classifier.name
            break
    return price_class, source, hints, failed


def _search(provider: PriceProvider, query: SearchQuery) -> Sequence[Listing] | ProviderError:
    try:
        return provider.search(query)
    except ProviderError as error:
        log.warning("listing search failed", extra={"provider": provider.name, "code": error.code})
        return error


def _judge(
    provider: PriceProvider,
    found: Sequence[Listing],
    query: SearchQuery,
    pending: _Pending,
    price_class: PriceClass,
    now: datetime,
) -> _Outcome:
    """The first accepted candidate in rank, or why none is."""
    main = provider.capabilities.main_exchanges

    def rank(listing: Listing):
        return candidate_rank(
            symbol=listing.symbol,
            exchange_code=listing.exchange_code,
            exchange_name=listing.exchange,
            matched_by=listing.matched_by,
            preferred=query.exchanges,
            main=main,
        )

    reasons: list[Rejection] = []
    for listing in sorted(found, key=rank):  # the engine's order is not trusted (D-125)
        reason = rejection(
            listing,
            currency=pending.currency,
            price_class=price_class,
            declared_units=provider.capabilities.units,
            today=now.date(),
        )
        if reason is None:
            return _Outcome(provider.name, "OK", listing=listing)
        reasons.append(reason)
    if reasons and reasons[0] in _MISMATCH:
        return _Outcome(provider.name, "MISMATCH", mismatch=_MISMATCH[reasons[0]])
    return _Outcome(provider.name, "NOT_FOUND")


def _choose(
    engine: Engine,
    engines: BuiltEngines,
    exchanges: Sequence[str],
    now: datetime,
    pending: _Pending,
    result: ResolveResult,
) -> None:
    if not any(
        _due(pending.known.get(p.name), now) for _, p in _orders(engines, pending.price_class)
    ):
        result.no_engine += 1
        return
    price_class, source, hints, failed = _classify(engines, pending.isin, result)
    if pending.price_class is not None:
        price_class, source = pending.price_class, None  # kept as stored
    elif price_class is None and failed:
        result.postponed += 1
        return
    query = SearchQuery.by_isin(pending.isin, hints, exchanges)
    # One search per engine; an engine in both orders (unknown class) has the same answer
    # judged under each class in turn. The last judgement of an engine is what is recorded.
    answers: dict[str, Sequence[Listing] | ProviderError] = {}
    outcomes: dict[str, _Outcome] = {}
    chosen_class: PriceClass | None = None
    for order_class, provider in _orders(engines, price_class):
        if not _due(pending.known.get(provider.name), now):
            continue
        if provider.name not in answers:
            answers[provider.name] = _search(provider, query)
            if isinstance(failure := answers[provider.name], ProviderError):
                result.errors[failure.code] += 1
        answer = answers[provider.name]
        if isinstance(answer, ProviderError):
            outcomes[provider.name] = _Outcome(provider.name, "FAILING", error=answer.code)
            continue
        outcome = _judge(provider, answer, query, pending, order_class, now)
        outcomes[provider.name] = outcome
        if outcome.status == "OK":
            chosen_class = order_class
            break
    _record(
        engine, pending, list(outcomes.values()), price_class, source, chosen_class, now, result
    )


def _record(
    engine: Engine,
    pending: _Pending,
    outcomes: list[_Outcome],
    price_class: PriceClass | None,
    source: str | None,
    chosen_class: PriceClass | None,
    now: datetime,
    result: ResolveResult,
) -> None:
    with db_transaction(engine, write=True) as session:
        instrument = session.get(Instrument, pending.id)
        if instrument is None:
            return  # deleted meanwhile
        if instrument.price_class is None:
            if source is not None and price_class is not None:
                instruments.set_price_class(session, instrument, price_class, source)
                result.classified[source] += 1
            elif chosen_class is not None:
                instruments.set_price_class(
                    session, instrument, chosen_class, instruments.CLASS_FROM_UNIT
                )
                result.classified[instruments.CLASS_FROM_UNIT] += 1
        chosen = None
        for outcome in outcomes:
            row = instruments.get_listing(session, pending.id, outcome.provider)
            if outcome.status == "FAILING":
                instruments.save_listing(
                    session,
                    pending.id,
                    outcome.provider,
                    status="FAILING",
                    last_error_code=outcome.error,
                    failure_count=(row.failure_count if row else 0) + 1,
                )
                continue
            listing = outcome.listing
            saved = instruments.save_listing(
                session,
                pending.id,
                outcome.provider,
                status=outcome.status,
                mismatch=outcome.mismatch.value if outcome.mismatch else None,
                symbol=listing.symbol if listing else None,
                exchange=listing.exchange if listing else None,
                exchange_code=listing.exchange_code if listing else None,
                currency=listing.currency if listing else None,
                unit=listing.unit.value if listing else None,
                matched_by=listing.matched_by.value if listing else None,
                checked_at=now,
                last_error_code=None,
                failure_count=0,
            )
            if listing is not None:
                chosen = saved
        if chosen is not None:
            if instruments.active_listing(session, pending.id) is None:
                instruments.activate(session, chosen, now)
                result.chosen += 1
        elif not outcomes:
            result.no_engine += 1  # e.g. a bond, now classified, and no bond engine
        elif all(outcome.status != "FAILING" for outcome in outcomes):
            result.not_found += 1
