"""Market data service: linking assets to instruments, classifying instruments, choosing and
switching their listings, and fetching closes (D-116, D-121, D-122, D-125, D-133, D-134,
D-136).

Runs inside the queue's jobs only, never inside a request (D-125: no response may tell a user
whether another user holds an ISIN). Everything here is internal: nothing is returned to a
user, and logs and results carry engine names, error codes and counts, never an ISIN.

**Linking** gives every asset with an ISIN the instrument of its (ISIN, quote currency):
local work in one short write transaction.

**The walk** (`resolve_listings`, every linked instrument, each run): the engines of the
instrument's class order are taken in turn; an engine whose last search accepted a listing
(its check is `OK`) offers that listing without a new search; an engine never asked, failing
since, or whose `NOT_FOUND` or `MISMATCH` is older than `RETRY_DAYS` is searched (the re-check
job, `recheck`, also searches the `OK` ones again, so a changed exchange order applies). The
first usable listing is the instrument's **target**.
1. *Class* (D-133): before any search, the classifiers are asked in order; the first class
   answered for that ISIN is kept with the engine's name. Only a classifier that carries the
   ISIN back (`echoes_isin`) passes its identifiers to the price engines as hints (D-136).
   The instrument waits for the next run when a classifier fails and none answered a class (a
   class inferred from a unit meanwhile could send a bond to an equity engine), and when a
   classifier carrying the ISIN back fails, even with a class known (a search without its
   hints is incomplete and would be recorded as not found for 30 days).
2. *Order*: the engines of the instrument's class; without a class, the `BOND` order first,
   then `ETF_STOCK`, and the class is then inferred from the accepted listing's unit (`UNIT`).
   When a bond engine fails in that walk, the instrument waits for the next run instead of
   going on to the `ETF_STOCK` order.
3. *Listing* (D-125, D-136): a searched engine's candidates are put in the fixed rank
   (`ranking.py`) and the first one `acceptance.rejection` accepts is its listing. An engine
   in both orders is searched once and its answer judged under each class.
4. *What is recorded per engine searched*, in its check (one per instrument and engine): `OK`
   with the accepted symbol, the listing being stored in `instrument_listings`; `NOT_FOUND`
   when nothing was found or nothing had a recent close; `MISMATCH` (`CURRENCY`, or `UNIT` for
   a unit that the engine does not declare or that does not fit the class) when candidates
   exist but none fits, judged on the best-ranked one; `FAILING` with the error code on a
   provider error (`ADAPTER_ERROR` for any other exception in an adapter: one engine's bug
   never stops the run, and its message, which could hold an ISIN, is not logged), the next
   engine being asked in the same run. A failed search of an engine whose check was `OK`
   keeps its listing (a passing error never takes a working listing away).

**Make before break** (D-134): an instrument without an active listing gets its target at
once. Otherwise a target other than the active listing is first back-filled to the full
horizon (`update_closes`); only then `switch_listings` activates it, in one transaction that
also deletes the other listings of the target's engine (and their closes). When the active
listing's engine is still enabled in the instrument's order, the **level check** comes
first: on the days both listings have among the last `LEVEL_WINDOW` stored days of the
active one, at least `LEVEL_MIN_DAYS` of them, a median ratio new/active further than
`LEVEL_TOLERANCE` from 1 refuses the target (`MISMATCH` `LEVEL` on it and on its engine's
check, searched again after `RETRY_DAYS`) and the active listing stays. With fewer common
days the switch happens. An asset reads only the active listing's closes: never stitched.

**Closes** (`update_closes`, D-119, D-122): each active listing, and each target waiting to
be back-filled, is fetched from its last `OVERLAP_DAYS` stored days (or from the horizon for a
new one) up to today. An answer in another currency or unit than the listing's stores
nothing and marks the listing `MISMATCH` (cleared by the next compatible answer); an error
marks it `FAILING` and keeps its closes; a symbol the provider no longer knows marks it
`NOT_FOUND` and makes its engine searched again on the next run (the listing stays active
until a replacement is ready).

Network discipline (D-121): what to do is read in a short read transaction, engines are
called with no transaction open, and each outcome is written in its own short write
transaction. There, rows are read again: an instrument deleted meanwhile is left alone, its
class is set only if still unknown, and a listing is activated only if still the target.
The queue runs one job at a time (D-121).
"""

import logging
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, delete, select

from portfolio_tracker.db import db_transaction
from portfolio_tracker.db.models import (
    Instrument,
    InstrumentEngineCheck,
    InstrumentListing,
    InstrumentPrice,
)
from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.acceptance import Rejection, rejection
from portfolio_tracker.marketdata.base import (
    ErrorCode,
    Identifier,
    Listing,
    PriceClass,
    PriceProvider,
    ProviderError,
    SearchQuery,
)
from portfolio_tracker.marketdata.ranking import candidate_rank
from portfolio_tracker.marketdata.registry import BuiltEngines
from portfolio_tracker.marketdata.units import MismatchKind, fits_class, to_stored
from portfolio_tracker.repositories import instruments
from portfolio_tracker.repositories.instruments import StoredClose
from portfolio_tracker.services.fx_rates import fetch_start

log = logging.getLogger(__name__)

# An engine that had nothing usable for an instrument (NOT_FOUND, or MISMATCH: unfit
# candidates or a listing refused by the level check) is searched again after this many
# days (D-134).
RETRY_DAYS = 30
# Unknown class: the bond order first, then the ETF/stock one (D-133).
UNKNOWN_CLASS_ORDER = (PriceClass.BOND, PriceClass.ETF_STOCK)
# The level check before a switch while the active listing's engine is still enabled
# (D-134): the median ratio new/active on the common days among the active listing's last
# LEVEL_WINDOW stored days, when there are at least LEVEL_MIN_DAYS, must be within
# LEVEL_TOLERANCE of 1.
LEVEL_TOLERANCE = Decimal("0.05")
LEVEL_MIN_DAYS = 3
LEVEL_WINDOW = 10
_RETRIED = frozenset({"NOT_FOUND", "MISMATCH"})
_USABLE = frozenset({"OK", "FAILING"})  # listing states a target may be in
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
    chosen: int = 0  # instruments that got their first listing
    not_found: int = 0  # every engine searched answered, none with an accepted listing
    postponed: int = 0  # waiting for the next run (a classifier or a bond engine failed)
    no_engine: int = 0  # nothing to ask (no engine for the class, or all waiting to retry)
    classified: Counter[str] = field(default_factory=Counter)  # by source: engine or UNIT
    errors: Counter[str] = field(default_factory=Counter)  # by error code
    # Targets waiting to replace an active listing: (instrument, listing), back-filled by
    # `update_closes` and switched by `switch_listings` (D-134). Ids stay inside the job.
    targets: list[tuple[UUID, UUID]] = field(default_factory=list)


@dataclass
class ClosesResult:
    """Counts only."""

    updated: int = 0  # listings whose closes were stored
    stored: int = 0  # closes written (fetched again ones included)
    mismatch: int = 0  # answers in another currency or unit: nothing stored
    errors: Counter[str] = field(default_factory=Counter)


@dataclass
class SwitchResult:
    """Counts only."""

    switched: int = 0  # targets made active
    level_refused: int = 0  # targets refused by the level check
    waiting: int = 0  # targets not back-filled yet


@dataclass(frozen=True)
class _Known:
    """The last search of one engine for an instrument (its check)."""

    status: str
    checked_at: datetime | None
    symbol: str | None


@dataclass(frozen=True)
class _Stored:
    """A stored listing, as read before the network calls."""

    id: UUID
    status: str
    is_active: bool


@dataclass(frozen=True)
class _Pending:
    """A linked instrument, as read before the network calls."""

    id: UUID
    isin: str
    currency: str
    price_class: PriceClass | None
    known: dict[str, _Known]
    listings: dict[tuple[str, str], _Stored]
    active: tuple[str, str] | None  # (engine, symbol) of the active listing


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


def resolve_listings(
    engine: Engine,
    engines: BuiltEngines,
    exchanges: Sequence[str],
    now: datetime,
    *,
    recheck: bool = False,
) -> ResolveResult:
    """Link new assets, then walk every linked instrument's order: an instrument without an
    active listing gets its target at once; a different target for one with an active
    listing is returned in `targets`, to be back-filled and switched (make before break)."""
    result = ResolveResult()
    result.linked, result.created = link_assets(engine)
    with db_transaction(engine, write=False) as session:
        pending = [
            _snapshot(session, instrument) for instrument in instruments.linked_instruments(session)
        ]
    for item in pending:
        _walk(engine, engines, exchanges, now, item, result, recheck)
    return result


def _snapshot(session, instrument: Instrument) -> _Pending:
    stored = instruments.listings(session, instrument.id)
    active = next(((x.provider, x.symbol) for x in stored if x.is_active), None)
    return _Pending(
        instrument.id,
        instrument.isin,
        instrument.quote_currency,
        PriceClass(instrument.price_class) if instrument.price_class else None,
        {
            row.provider: _Known(row.status, row.checked_at, row.symbol)
            for row in instruments.checks(session, instrument.id)
        },
        {(x.provider, x.symbol): _Stored(x.id, x.status, x.is_active) for x in stored},
        active,
    )


def _due(known: _Known | None, now: datetime, recheck: bool) -> bool:
    """Whether an engine is to be searched (again) for an instrument."""
    if known is None or known.status == "FAILING":
        return True
    if known.status == "OK":
        return recheck  # its listing is offered without a search
    return known.checked_at is None or known.checked_at <= now - timedelta(days=RETRY_DAYS)


def _offered(pending: _Pending, provider: str, known: _Known | None) -> tuple[str, str] | None:
    """The listing an engine's `OK` check points to, when it is usable."""
    if known is None or known.status != "OK" or known.symbol is None:
        return None
    stored = pending.listings.get((provider, known.symbol))
    return (provider, known.symbol) if stored and stored.status in _USABLE else None


def _orders(
    engines: BuiltEngines, price_class: PriceClass | None
) -> list[tuple[PriceClass, PriceProvider]]:
    classes = (price_class,) if price_class else UNKNOWN_CLASS_ORDER
    return [(c, provider) for c in classes for provider in engines.prices(c)]


@dataclass(frozen=True)
class _Facts:
    """What the classifiers said about one ISIN."""

    price_class: PriceClass | None = None
    source: str | None = None  # the classifier that gave the class
    hints: tuple[Identifier, ...] = ()
    failed: bool = False  # a classifier failed
    hints_lost: bool = False  # a classifier carrying the ISIN back failed: no hints from it


def _classify(engines: BuiltEngines, isin: str, result: ResolveResult) -> _Facts:
    price_class: PriceClass | None = None
    source: str | None = None
    hints: tuple[Identifier, ...] = ()
    failed = hints_lost = False
    for classifier in engines.classifiers():
        try:
            facts = classifier.classify(isin)
        except Exception as error:  # an adapter bug must not stop the run (D-121)
            code = _code(error)
            failed = True
            hints_lost = hints_lost or classifier.capabilities.echoes_isin
            result.errors[code] += 1
            log.warning("classifier failed", extra={"provider": classifier.name, "code": code})
            continue
        if facts is None:
            continue
        if not hints and classifier.capabilities.echoes_isin:
            hints = facts.identifiers  # only an exact ISIN lookup may anchor a match (D-133)
        if facts.price_class is not None:
            price_class, source = facts.price_class, classifier.name
            break
    return _Facts(price_class, source, hints, failed, hints_lost)


def _code(error: Exception) -> str:
    """A provider error's code; any other exception is an adapter bug, reduced to a code (its
    message could hold an ISIN)."""
    return error.code if isinstance(error, ProviderError) else ErrorCode.ADAPTER_ERROR


def _search(provider: PriceProvider, query: SearchQuery) -> Sequence[Listing] | str:
    """The engine's candidates, or the error code of its failure."""
    try:
        return provider.search(query)
    except Exception as error:  # an adapter bug must not stop the run (D-121)
        code = _code(error)
        log.warning(
            "listing search failed",
            extra={"provider": provider.name, "code": code, "type": type(error).__name__},
        )
        return code


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


def _walk(
    engine: Engine,
    engines: BuiltEngines,
    exchanges: Sequence[str],
    now: datetime,
    pending: _Pending,
    result: ResolveResult,
    recheck: bool,
) -> None:
    order = _orders(engines, pending.price_class)
    searching = [p for _, p in order if _due(pending.known.get(p.name), now, recheck)]
    if not searching and not any(
        _offered(pending, p.name, pending.known.get(p.name)) for _, p in order
    ):
        if pending.active is None:
            result.no_engine += 1
        return
    facts = _Facts()
    if searching:
        facts = _classify(engines, pending.isin, result)
        if facts.hints_lost or (
            pending.price_class is None and facts.price_class is None and facts.failed
        ):
            # Without the hints the search is incomplete, and without a class a unit could
            # decide it: wait for the next run rather than record a wrong outcome (D-133).
            result.postponed += 1
            return
    price_class, source = facts.price_class, facts.source
    if pending.price_class is not None:
        price_class, source = pending.price_class, None  # kept as stored
    query = SearchQuery.by_isin(pending.isin, facts.hints, exchanges)
    # One search per engine; an engine in both orders (unknown class) has the same answer
    # judged under each class in turn. The last judgement of an engine is what is recorded.
    answers: dict[str, Sequence[Listing] | str] = {}
    outcomes: dict[str, _Outcome] = {}
    target: tuple[str, str] | None = None
    chosen_class: PriceClass | None = None
    postponed = bond_failed = False
    for order_class, provider in _orders(engines, price_class):
        known = pending.known.get(provider.name)
        if not _due(known, now, recheck):
            if (offered := _offered(pending, provider.name, known)) is not None:
                target, chosen_class = offered, order_class
                break
            continue
        if price_class is None and order_class is PriceClass.ETF_STOCK and bond_failed:
            # A bond engine failed: an equity listing must not set the class (D-133).
            postponed = True
            break
        if provider.name not in answers:
            answers[provider.name] = _search(provider, query)
            if isinstance(failure := answers[provider.name], str):
                result.errors[failure] += 1
        answer = answers[provider.name]
        if isinstance(answer, str):
            outcomes[provider.name] = _Outcome(provider.name, "FAILING", error=answer)
            bond_failed = bond_failed or order_class is PriceClass.BOND
            if (offered := _offered(pending, provider.name, known)) is not None:
                target, chosen_class = offered, order_class  # a passing error keeps it
                break
            continue
        outcome = _judge(provider, answer, query, pending, order_class, now)
        outcomes[provider.name] = outcome
        if outcome.status == "OK" and outcome.listing is not None:
            target, chosen_class = (provider.name, outcome.listing.symbol), order_class
            break
    if postponed:
        result.postponed += 1
    learned = pending.price_class is None and (source is not None or chosen_class is not None)
    if not outcomes and not learned and (target is None or target == pending.active):
        if target is None and pending.active is None:
            result.no_engine += 1
        return  # nothing new: no write
    _record(
        engine,
        pending,
        list(outcomes.values()),
        price_class,
        source,
        chosen_class,
        target,
        now,
        result,
    )


def _record(
    engine: Engine,
    pending: _Pending,
    outcomes: list[_Outcome],
    price_class: PriceClass | None,
    source: str | None,
    chosen_class: PriceClass | None,
    target: tuple[str, str] | None,
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
        for outcome in outcomes:
            _save_outcome(session, pending.id, outcome, now)
        if target is None:
            if pending.active is None:
                if not outcomes:
                    result.no_engine += 1  # e.g. a bond, now classified, and no bond engine
                elif all(outcome.status != "FAILING" for outcome in outcomes):
                    result.not_found += 1
            return
        listing = instruments.get_listing(session, pending.id, *target)
        if listing is None or listing.is_active:
            return
        active = instruments.active_listing(session, pending.id)
        if active is None:
            instruments.activate(session, listing, now)
            result.chosen += 1
        else:
            result.targets.append((pending.id, listing.id))


def _save_outcome(session, instrument_id: UUID, outcome: _Outcome, now: datetime) -> None:
    check = session.get(InstrumentEngineCheck, (instrument_id, outcome.provider))
    if outcome.status == "FAILING":
        failures = (check.failure_count if check else 0) + 1
        if check is not None and check.status == "OK":
            # A passing error never takes a working listing away: only counted.
            check.failure_count, check.last_error_code = failures, outcome.error
            return
        instruments.save_check(
            session,
            instrument_id,
            outcome.provider,
            status="FAILING",
            mismatch=None,
            last_error_code=outcome.error,
            failure_count=failures,
        )
        return
    listing = outcome.listing
    instruments.save_check(
        session,
        instrument_id,
        outcome.provider,
        status=outcome.status,
        symbol=listing.symbol if listing else None,
        mismatch=outcome.mismatch.value if outcome.mismatch else None,
        checked_at=now,
        last_error_code=None,
        failure_count=0,
    )
    if listing is not None:
        stored = instruments.get_listing(session, instrument_id, outcome.provider, listing.symbol)
        fields = {
            "exchange": listing.exchange,
            "exchange_code": listing.exchange_code,
            "currency": listing.currency,
            "unit": listing.unit.value,
            "matched_by": listing.matched_by.value,
        }
        if stored is not None and stored.status in ("MISMATCH", "NOT_FOUND"):
            fields |= {"status": "OK", "mismatch": None}  # accepted again: judged afresh
        instruments.save_listing(session, instrument_id, outcome.provider, listing.symbol, **fields)


# --- closes (D-119, D-122) --------------------------------------------------------------------


@dataclass(frozen=True)
class _ToFetch:
    id: UUID
    instrument_id: UUID
    provider: str
    symbol: str
    currency: str
    unit: QuoteUnit
    last_stored: object  # date | None


def update_closes(
    engine: Engine,
    engines: BuiltEngines,
    now: datetime,
    targets: Sequence[tuple[UUID, UUID]] = (),
) -> ClosesResult:
    """Fetch the closes of every active listing of a linked instrument and of each target
    waiting to be back-filled, each from its last stored days (D-122)."""
    result = ClosesResult()
    today = now.date()
    by_name = {p.name: p for c in PriceClass for p in engines.prices(c)}
    wanted = {listing_id for _, listing_id in targets}
    with db_transaction(engine, write=False) as session:
        linked = {i.id for i in instruments.linked_instruments(session)}
        rows = session.scalars(
            select(InstrumentListing).where(
                InstrumentListing.is_active | InstrumentListing.id.in_(wanted)
            )
        ).all()
        plan = [
            _ToFetch(
                row.id,
                row.instrument_id,
                row.provider,
                row.symbol,
                row.currency,
                QuoteUnit(row.unit),
                instruments.last_stored_day(session, row.id),
            )
            for row in rows
            if row.instrument_id in linked
        ]
    for item in plan:
        provider = by_name.get(item.provider)
        if provider is None:
            continue  # its engine is disabled: no call, the closes go stale (D-134)
        start = fetch_start(item.last_stored, today, provider.capabilities.history_years)
        if start > today:
            continue
        try:
            series = provider.daily_closes(item.symbol, start, today)
        except Exception as error:  # an adapter bug must not stop the run (D-121)
            code = _code(error)
            result.errors[code] += 1
            log.warning("closes fetch failed", extra={"provider": item.provider, "code": code})
            _closes_failed(engine, item, code)
            continue
        if (series.currency, series.unit) != (item.currency, item.unit):
            kind = MismatchKind.CURRENCY if series.currency != item.currency else MismatchKind.UNIT
            result.mismatch += 1
            log.warning("closes in another unit", extra={"provider": item.provider, "kind": kind})
            _set_listing(engine, item.id, status="MISMATCH", mismatch=kind.value)
            continue
        closes = [
            StoredClose(
                c.day,
                to_stored(c.close, series.unit),
                {"unit": series.unit.value}
                if series.unit is QuoteUnit.MINOR_CURRENCY_UNIT
                else None,
            )
            for c in series.closes
            if start <= c.day <= today
        ]
        with db_transaction(engine, write=True) as session:
            listing = session.get(InstrumentListing, item.id)
            if listing is None:
                continue  # deleted meanwhile
            result.stored += instruments.store_closes(session, item.id, closes)
            listing.status, listing.mismatch = "OK", None
            listing.last_success_at, listing.failure_count, listing.last_error_code = now, 0, None
            result.updated += 1
    return result


def _set_listing(engine: Engine, listing_id: UUID, **fields: object) -> None:
    with db_transaction(engine, write=True) as session:
        listing = session.get(InstrumentListing, listing_id)
        if listing is not None:
            for name, value in fields.items():
                setattr(listing, name, value)


def _closes_failed(engine: Engine, item: _ToFetch, code: str) -> None:
    with db_transaction(engine, write=True) as session:
        listing = session.get(InstrumentListing, item.id)
        if listing is None:
            return
        listing.failure_count += 1
        listing.last_error_code = code
        if code == ErrorCode.NOT_FOUND:
            # The provider no longer knows the symbol: its engine is searched again on the
            # next run; the listing stays active until a replacement is ready (D-134).
            listing.status, listing.mismatch = "NOT_FOUND", None
            instruments.save_check(
                session,
                item.instrument_id,
                item.provider,
                status="FAILING",
                mismatch=None,
                last_error_code=code,
            )
        elif listing.status != "MISMATCH":
            listing.status = "FAILING"


# --- switching (D-134) -------------------------------------------------------------------------


def level_ratio(session, active_id: UUID, target_id: UUID) -> Decimal | None:
    """The median ratio target/active on the common days among the active listing's last
    `LEVEL_WINDOW` stored days; None with fewer than `LEVEL_MIN_DAYS` of them."""
    recent = session.execute(
        select(InstrumentPrice.day, InstrumentPrice.close)
        .where(InstrumentPrice.listing_id == active_id)
        .order_by(InstrumentPrice.day.desc())
        .limit(LEVEL_WINDOW)
    ).all()
    old = {day: close for day, close in recent if close}
    new = dict(
        session.execute(
            select(InstrumentPrice.day, InstrumentPrice.close).where(
                InstrumentPrice.listing_id == target_id, InstrumentPrice.day.in_(old)
            )
        ).all()
    )
    ratios = [new[day] / old[day] for day in old if day in new]
    if len(ratios) < LEVEL_MIN_DAYS:
        return None
    return statistics.median(ratios)


def switch_listings(
    engine: Engine,
    engines: BuiltEngines,
    targets: Sequence[tuple[UUID, UUID]],
    now: datetime,
) -> SwitchResult:
    """Activate each back-filled target in place of the active listing (make before break,
    D-134), after the level check when the active listing's engine is still enabled."""
    result = SwitchResult()
    for instrument_id, target_id in targets:
        with db_transaction(engine, write=True) as session:
            instrument = session.get(Instrument, instrument_id)
            target = session.get(InstrumentListing, target_id)
            if instrument is None or target is None or target.is_active:
                continue
            if target.last_success_at is None or target.status not in _USABLE:
                result.waiting += 1  # not back-filled yet: the active listing stays
                continue
            active = instruments.active_listing(session, instrument_id)
            if active is not None and _still_trusted(engines, instrument, active.provider):
                ratio = level_ratio(session, active.id, target.id)
                if ratio is not None and abs(ratio - 1) > LEVEL_TOLERANCE:
                    target.status, target.mismatch = "MISMATCH", MismatchKind.LEVEL.value
                    instruments.save_check(
                        session,
                        instrument_id,
                        target.provider,
                        status="MISMATCH",
                        mismatch=MismatchKind.LEVEL.value,
                        checked_at=now,
                    )
                    result.level_refused += 1
                    log.warning(
                        "listing switch refused by the level check",
                        extra={"provider": target.provider},
                    )
                    continue
            instruments.activate(session, target, now)
            session.execute(
                delete(InstrumentListing).where(
                    InstrumentListing.instrument_id == instrument_id,
                    InstrumentListing.provider == target.provider,
                    InstrumentListing.id != target.id,
                )
            )  # the engine's other listings and, by cascade, their closes
            result.switched += 1
    return result


def _still_trusted(engines: BuiltEngines, instrument: Instrument, provider: str) -> bool:
    """Whether the active listing's engine is still enabled in the instrument's order: then
    a switch is a reorder and needs the level check; otherwise the administrator has
    disabled it, or the class changed, and the switch happens without it (D-134)."""
    price_class = PriceClass(instrument.price_class) if instrument.price_class else None
    return any(p.name == provider for _, p in _orders(engines, price_class))


def recheck_classes(engine: Engine, engines: BuiltEngines) -> Counter[str]:
    """The re-check job's first step (D-133): a class inferred from a unit (`UNIT`) is asked of
    the classifiers again; a class answered replaces it with the engine's name. When the class
    changes, an active listing whose unit does not fit the new class stops being active (it
    would feed a series of the wrong kind); the walk then looks for one in the new order.
    Returns counts: `reclassified`, `deactivated` and error codes."""
    counts: Counter[str] = Counter()
    if not engines.classifiers():
        return counts
    with db_transaction(engine, write=False) as session:
        inferred = [
            (i.id, i.isin)
            for i in instruments.linked_instruments(session)
            if i.class_source == instruments.CLASS_FROM_UNIT
        ]
    for instrument_id, isin in inferred:
        result = ResolveResult()
        facts = _classify(engines, isin, result)
        counts.update(result.errors)
        if facts.price_class is None or facts.source is None:
            continue
        with db_transaction(engine, write=True) as session:
            instrument = session.get(Instrument, instrument_id)
            if instrument is None or instrument.class_source != instruments.CLASS_FROM_UNIT:
                continue
            changed = instrument.price_class != facts.price_class.value
            instruments.set_price_class(session, instrument, facts.price_class, facts.source)
            counts["reclassified"] += 1
            active = instruments.active_listing(session, instrument_id)
            if (
                changed
                and active is not None
                and not fits_class(facts.price_class, QuoteUnit(active.unit))
            ):
                active.is_active = False
                counts["deactivated"] += 1
                log.warning(
                    "active listing does not fit the corrected class",
                    extra={"provider": active.provider},
                )
    return counts


def refresh_prices(
    engine: Engine,
    engines: BuiltEngines,
    exchanges: Sequence[str],
    now: datetime,
    *,
    recheck: bool = False,
) -> tuple[ResolveResult, ClosesResult, SwitchResult]:
    """One price run: listings, then closes (back-fill included), then switches."""
    resolved = resolve_listings(engine, engines, exchanges, now, recheck=recheck)
    closes = update_closes(engine, engines, now, resolved.targets)
    switched = switch_listings(engine, engines, resolved.targets, now)
    return resolved, closes, switched
