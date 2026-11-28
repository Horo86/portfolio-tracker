"""Market data service: linking assets to instruments, classifying instruments, choosing and
switching their listings, and fetching closes (D-116, D-121, D-122, D-125, D-133, D-134,
D-136, D-201).

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
3. *Listing* (D-125, D-136, D-152): a searched engine's candidates are put in the fixed
   rank (`ranking.py`, the instrument's currency choosing the home exchanges) and the first
   one `acceptance.rejection` accepts is its listing. An engine
   in both orders is searched once and its answer judged under each class.
4. *What is recorded per engine searched*, in its check (one per instrument and engine): `OK`
   with the accepted symbol, the listing being stored in `instrument_listings`; `NOT_FOUND`
   when nothing was found or nothing had a recent close; `MISMATCH` (`CURRENCY`, or `UNIT` for
   a unit that the engine does not declare or that does not fit the class) when candidates
   exist but none fits, judged on the best-ranked one; `FAILING` with the error code on a
   provider error (`ADAPTER_ERROR` for any other exception in an adapter: one engine's bug
   never stops the run, and its message, which could hold an ISIN, is not logged), the next
   engine being asked in the same run. A failed search of an engine whose check was `OK`
   keeps its listing (a passing error never takes a working listing away), and a search that
   does not confirm the engine's usable active listing (`NOT_FOUND`, `MISMATCH`) leaves its
   check `OK` on that listing: it never takes it away. A stored listing is offered only when
   its unit fits the class of the order it is offered in.

**Re-check marks** (D-152): a migration that changes the listing rank marks every existing
instrument (`listing_recheck_at`). Each run walks at most `RECHECK_PER_RUN` marked instruments,
oldest mark first, as in the re-check job (the engines with an accepted listing searched
again); a re-check job walks them all. A mark is cleared once its walk had no classifier or
engine search fail and did not postpone it; otherwise it gets the run's time, so it goes to the
back. Make before break applies as for any target.

**Make before break** (D-134): an instrument without an active listing gets its target at
once (the other listings of its engine, and their closes, are deleted). Otherwise a target
other than the active listing is first back-filled to the full horizon (`update_closes`);
`switch_listings` activates it only when that run stored closes for it and its last close
is at most `OVERLAP_DAYS` older than the active listing's (never an empty or stale series),
in one transaction that also deletes the other listings of the target's engine (and their
closes). When the active listing's engine is still enabled in the instrument's order and
its symbol is still known to the provider, the **level check** comes first: on the days
both listings have among the last `LEVEL_WINDOW` stored days of the active one, at least
`LEVEL_MIN_DAYS` of them, a median ratio new/active further than `LEVEL_TOLERANCE` from 1
refuses the target and the active listing stays. The refused target is a `MISMATCH`
`LEVEL` and loses its closes; another engine's check gets the same mismatch (searched again
after `RETRY_DAYS`), while the active listing's own engine keeps its check on the active
listing. With fewer common days the switch happens. An asset reads only the active
listing's closes: never stitched.

**Closes** (`update_closes`, D-119, D-122, D-201): each active listing, each target waiting
to be back-filled, and each listing an `OK` check points to whose last answer was in another
currency or unit (so its mismatch clears itself), is fetched from the first of its last
`REBASE_WINDOW` stored days (or from the horizon for a new one) up to today. An answer in
another currency or unit than the listing's stores nothing and marks the listing `MISMATCH`
(cleared by the next compatible answer); an error marks it `FAILING` and keeps its closes; a
symbol the provider no longer knows marks it `NOT_FOUND` and makes its engine searched again
on the next run (the listing stays active until a replacement is ready, which then needs no
level check).

**One uniform series** (D-201): a provider adjusts past closes for later splits, so the
answer is compared with the stored window first (`rebase_needed`; the newest stored day, which
may be a partial bar, is left out). When it shows the series rescaled, nothing of it is stored:
the listing is marked (`refetch_requested_at`) and its whole series is downloaded again in the
same run, once (`_refetch`, a `REBASE`), and replaces the stored closes in one write that also
clears the mark. A listing marked at the start of the run (by the upgrade to 1.2.0, or by an
earlier run) is downloaded whole first, oldest mark first (a `MARK`: the stored days after the
answer's last day are kept, unless the answer itself shows the series rescaled). A full answer
that is empty, ends more than `OVERLAP_DAYS` before the stored series, or starts more than
`REFETCH_SHORT_START_DAYS` after it (while the mark is at most `REFETCH_ACCEPT_AFTER_DAYS`
old) is refused (`refetch_refusal`); a refused or failed full download keeps the mark and the
stored closes, and the daily update from the stored window still happens in that run, so a
listing never stops getting closes. A disabled engine's listing is not called: its mark stays.

Network discipline (D-121): what to do is read in a short read transaction, engines are
called with no transaction open, and each outcome is written in its own short write
transaction. There, rows are read again: an instrument deleted meanwhile is left alone, its
class is set only if still unknown, and a listing is activated only if still the target.
The queue runs one job at a time (D-121).
"""

import logging
import statistics
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
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
from portfolio_tracker.marketdata.base import (
    ErrorCode,
    Listing,
    PriceClass,
    PriceProvider,
    SearchQuery,
)
from portfolio_tracker.marketdata.registry import BuiltEngines
from portfolio_tracker.marketdata.units import MismatchKind, fits_class, to_stored
from portfolio_tracker.repositories import instruments
from portfolio_tracker.repositories.instruments import StoredClose
from portfolio_tracker.services.fx_rates import OVERLAP_DAYS, fetch_start
from portfolio_tracker.services.listing_choice import (
    Facts,
    Outcome,
    classify,
    error_code,
    judge,
    orders,
    search,
)

log = logging.getLogger(__name__)

# An engine that had nothing usable for an instrument (NOT_FOUND, or MISMATCH: unfit
# candidates or a listing refused by the level check) is searched again after this many
# days (D-134).
RETRY_DAYS = 30
# The level check before a switch while the active listing's engine is still enabled
# (D-134): the median ratio new/active on the common days among the active listing's last
# LEVEL_WINDOW stored days, when there are at least LEVEL_MIN_DAYS, must be within
# LEVEL_TOLERANCE of 1.
LEVEL_TOLERANCE = Decimal("0.05")
LEVEL_MIN_DAYS = 3
LEVEL_WINDOW = 10
# A uniform series per listing (D-201): each run fetches again from the first of a listing's
# last REBASE_WINDOW stored days and compares them, the newest left out, with the answer; a day
# further than REBASE_TOLERANCE from its stored close means the provider rescaled the series,
# which is then downloaded again whole. A full answer starting more than
# REFETCH_SHORT_START_DAYS after the stored series is refused until the mark is more than
# REFETCH_ACCEPT_AFTER_DAYS old. FX keeps OVERLAP_DAYS (D-122).
REBASE_WINDOW = 15
REBASE_TOLERANCE = Decimal("0.01")
REFETCH_SHORT_START_DAYS = 31
REFETCH_ACCEPT_AFTER_DAYS = 7
# Instruments marked for a re-check by an upgrade (D-152) searched again in one run, oldest
# mark first, so an upgrade does not send every instrument's searches at once.
RECHECK_PER_RUN = 20
_RETRIED = frozenset({"NOT_FOUND", "MISMATCH"})
_USABLE = frozenset({"OK", "FAILING"})  # listing states a target may be in


@dataclass
class ResolveResult:
    """Counts only (they go to the job log, D-121)."""

    linked: int = 0  # assets linked to an instrument
    created: int = 0  # instruments created for them
    chosen: int = 0  # instruments that got their first listing
    not_found: int = 0  # every engine searched answered, none with an accepted listing
    postponed: int = 0  # waiting for the next run (a classifier or a bond engine failed)
    no_engine: int = 0  # nothing to ask (no engine for the class, or all waiting to retry)
    rechecked: int = 0  # marked instruments searched again (D-152)
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
    refetched: int = 0  # whole series downloaded again and stored (D-201)
    rebased: int = 0  # series found rescaled by the comparison
    refetch_refused: int = 0  # full answers refused (empty, stale end, short start)
    shortened: int = 0  # full answers accepted with a later start once the mark was old
    errors: Counter[str] = field(default_factory=Counter)
    # Listings that stored at least one close in this run: only these may be switched to
    # (D-134). Ids stay inside the job.
    filled: set[UUID] = field(default_factory=set)


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
    unit: QuoteUnit


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
    postponed: bool = False  # the last walk postponed it (`postponed_since` is set)
    recheck_mark: datetime | None = None  # a re-check mark of an upgrade (D-152)


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
    classified: Mapping[UUID, "Facts"] | None = None,
) -> ResolveResult:
    """Link new assets, then walk every linked instrument's order: an instrument without an
    active listing gets its target at once; a different target for one with an active
    listing is returned in `targets`, to be back-filled and switched (make before break).
    `classified` holds what the classifiers said in this run (`recheck_classes`), so an
    instrument is not classified twice."""
    result = ResolveResult()
    result.linked, result.created = link_assets(engine)
    with db_transaction(engine, write=False) as session:
        pending = [
            _snapshot(session, instrument) for instrument in instruments.linked_instruments(session)
        ]
    marked = sorted(
        (p for p in pending if p.recheck_mark), key=lambda p: (p.recheck_mark, str(p.id))
    )
    again = {p.id for p in (marked if recheck else marked[:RECHECK_PER_RUN])}
    for item in pending:
        errors, postponed = result.errors.total(), result.postponed
        searched = recheck or item.id in again
        _walk(engine, engines, exchanges, now, item, result, searched, classified or {})
        if item.id in again:
            result.rechecked += 1
            complete = result.errors.total() == errors and result.postponed == postponed
            _settle_mark(engine, item, None if complete else now)
    return result


def _settle_mark(engine: Engine, pending: "_Pending", mark: datetime | None) -> None:
    """Clear an instrument's re-check mark after a complete search, or move it to the back of
    the queue (`mark`: the run's time) when a search failed or it was postponed (D-152)."""
    with db_transaction(engine, write=True) as session:
        instrument = session.get(Instrument, pending.id)
        if instrument is not None and instrument.listing_recheck_at == pending.recheck_mark:
            instrument.listing_recheck_at = mark


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
        {
            (x.provider, x.symbol): _Stored(x.id, x.status, x.is_active, QuoteUnit(x.unit))
            for x in stored
        },
        active,
        instrument.postponed_since is not None,
        instrument.listing_recheck_at,
    )


def _due(known: _Known | None, now: datetime, recheck: bool) -> bool:
    """Whether an engine is to be searched (again) for an instrument."""
    if known is None or known.status == "FAILING":
        return True
    if known.status == "OK":
        return recheck  # its listing is offered without a search
    return known.checked_at is None or known.checked_at <= now - timedelta(days=RETRY_DAYS)


def _offered(
    pending: _Pending, provider: str, known: _Known | None, price_class: PriceClass
) -> tuple[str, str] | None:
    """The listing an engine's `OK` check points to, when it is usable and its unit fits the
    class of the order it is offered in (D-133)."""
    if known is None or known.status != "OK" or known.symbol is None:
        return None
    return _usable(pending, (provider, known.symbol), price_class)


def _kept(pending: _Pending, provider: str, price_class: PriceClass) -> tuple[str, str] | None:
    """The instrument's active listing when `provider` holds it and it is usable: a search of
    that engine that does not confirm it never takes it away (D-134)."""
    if pending.active is None or pending.active[0] != provider:
        return None
    return _usable(pending, pending.active, price_class)


def _usable(
    pending: _Pending, key: tuple[str, str], price_class: PriceClass
) -> tuple[str, str] | None:
    stored = pending.listings.get(key)
    if stored is None or stored.status not in _USABLE or not fits_class(price_class, stored.unit):
        return None
    return key


def _walk(
    engine: Engine,
    engines: BuiltEngines,
    exchanges: Sequence[str],
    now: datetime,
    pending: _Pending,
    result: ResolveResult,
    recheck: bool,
    classified: Mapping[UUID, Facts],
) -> None:
    order = orders(engines, pending.price_class)
    searching = [p for _, p in order if _due(pending.known.get(p.name), now, recheck)]
    if not searching and not any(
        _offered(pending, p.name, pending.known.get(p.name), c) for c, p in order
    ):
        if pending.active is None:
            result.no_engine += 1
        if pending.postponed:  # nothing left to search: no longer waiting for a run
            _set_postponed(engine, pending.id, None)
        return
    facts = Facts()
    if searching:
        facts = classified.get(pending.id) or classify(engines, pending.isin, result.errors)
        if facts.hints_lost or (
            pending.price_class is None and facts.price_class is None and facts.failed
        ):
            # Without the hints the search is incomplete, and without a class a unit could
            # decide it: wait for the next run rather than record a wrong outcome (D-133).
            result.postponed += 1
            if not pending.postponed:
                _set_postponed(engine, pending.id, now)
            return
    price_class, source = facts.price_class, facts.source
    if pending.price_class is not None:
        price_class, source = pending.price_class, None  # kept as stored
    query = SearchQuery.by_isin(pending.isin, facts.hints, exchanges, pending.currency)
    # One search per engine; an engine in both orders (unknown class) has the same answer
    # judged under each class in turn. The last judgement of an engine is what is recorded.
    answers: dict[str, Sequence[Listing] | str] = {}
    outcomes: dict[str, Outcome] = {}
    target: tuple[str, str] | None = None
    chosen_class: PriceClass | None = None
    postponed = bond_failed = False
    for order_class, provider in orders(engines, price_class):
        known = pending.known.get(provider.name)
        if not _due(known, now, recheck):
            if (offered := _offered(pending, provider.name, known, order_class)) is not None:
                target, chosen_class = offered, order_class
                break
            continue
        if price_class is None and order_class is PriceClass.ETF_STOCK and bond_failed:
            # A bond engine failed: an equity listing must not set the class (D-133).
            postponed = True
            break
        if provider.name not in answers:
            answers[provider.name] = search(provider, query)
            if isinstance(failure := answers[provider.name], str):
                result.errors[failure] += 1
        answer = answers[provider.name]
        if isinstance(answer, str):
            outcomes[provider.name] = Outcome(provider.name, "FAILING", error=answer)
            bond_failed = bond_failed or order_class is PriceClass.BOND
            if (offered := _offered(pending, provider.name, known, order_class)) is not None:
                target, chosen_class = offered, order_class  # a passing error keeps it
                break
            continue
        outcome = judge(provider, answer, query, pending.currency, order_class, now)
        outcomes[provider.name] = outcome
        if outcome.status == "OK" and outcome.listing is not None:
            target, chosen_class = (provider.name, outcome.listing.symbol), order_class
            break
        if (kept := _kept(pending, provider.name, order_class)) is not None:
            target, chosen_class = kept, order_class
            break
    if postponed:
        result.postponed += 1
    learned = pending.price_class is None and (source is not None or chosen_class is not None)
    unchanged = target is None or target == pending.active
    if not outcomes and not learned and unchanged and postponed == pending.postponed:
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
        postponed,
    )


def _set_postponed(engine: Engine, instrument_id: UUID, since: datetime | None) -> None:
    """Mark an instrument as waiting for the next run (`since`), or clear the mark (None)."""
    with db_transaction(engine, write=True) as session:
        instrument = session.get(Instrument, instrument_id)
        if instrument is not None and (since is None or instrument.postponed_since is None):
            instrument.postponed_since = since


def _record(
    engine: Engine,
    pending: _Pending,
    outcomes: list[Outcome],
    price_class: PriceClass | None,
    source: str | None,
    chosen_class: PriceClass | None,
    target: tuple[str, str] | None,
    now: datetime,
    result: ResolveResult,
    postponed: bool = False,
) -> None:
    with db_transaction(engine, write=True) as session:
        instrument = session.get(Instrument, pending.id)
        if instrument is None:
            return  # deleted meanwhile
        instrument.postponed_since = (instrument.postponed_since or now) if postponed else None
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
            instruments.delete_other_listings(session, listing)
            result.chosen += 1
        else:
            result.targets.append((pending.id, listing.id))


def _save_outcome(session, instrument_id: UUID, outcome: Outcome, now: datetime) -> None:
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
    active = instruments.active_listing(session, instrument_id)
    if (
        outcome.status != "OK"
        and active is not None
        and active.provider == outcome.provider
        and active.status in _USABLE
    ):
        # A search that does not confirm the engine's active listing never takes it away
        # (D-134): its check stays on that listing.
        _confirm_active(session, active, now)
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


def _confirm_active(session, active: InstrumentListing, now: datetime) -> None:
    instruments.save_check(
        session,
        active.instrument_id,
        active.provider,
        status="OK",
        symbol=active.symbol,
        mismatch=None,
        checked_at=now,
        last_error_code=None,
        failure_count=0,
    )


# --- closes (D-119, D-122, D-201) -------------------------------------------------------------


class Refetch(StrEnum):
    """Why a listing's whole series is downloaded again (D-201)."""

    MARK = "MARK"  # a mark found at the start of the run (the upgrade's, or an earlier run's)
    REBASE = "REBASE"  # this run's comparison found the series rescaled


@dataclass(frozen=True)
class _ToFetch:
    id: UUID
    instrument_id: UUID
    provider: str
    symbol: str
    currency: str
    unit: QuoteUnit
    overlap: date | None  # the first of its last REBASE_WINDOW stored days, fetched again
    window: Mapping[date, Decimal]  # its stored closes from `overlap` on
    first: date | None  # its first stored day
    mark: datetime | None  # its refetch mark (D-201)

    @property
    def last(self) -> date | None:
        return max(self.window) if self.window else None


def rebase_needed(
    stored: Mapping[date, Decimal], answer: Mapping[date, Decimal], newest: date
) -> bool:
    """Whether an answer shows the stored series rescaled (D-201). The comparable days are the
    stored days of the window, without the newest one (it may be a partial bar), that the answer
    also has; a day absent on either side (a null row) is not comparable. Any comparable day
    further than `REBASE_TOLERANCE` from its stored close means a rebase; so does an answer that
    brings new days with no comparable day (uniformity is then unproven)."""
    comparable = [day for day, close in stored.items() if day < newest and day in answer and close]
    if not comparable:
        return any(day > newest for day in answer)
    return any(abs(answer[day] / stored[day] - 1) > REBASE_TOLERANCE for day in comparable)


def refetch_refusal(
    first: date | None,
    last: date | None,
    stored_first: date | None,
    stored_last: date | None,
    mark: datetime,
    now: datetime,
) -> str | None:
    """Why a full answer (its first and last close) must not replace the stored series, or None
    (D-201): `EMPTY`; `STALE_END`, its last close more than `OVERLAP_DAYS` before the stored
    one; `SHORT_START`, its first close more than `REFETCH_SHORT_START_DAYS` after the stored
    one while the mark is at most `REFETCH_ACCEPT_AFTER_DAYS` old (then the shorter history is
    accepted: the older days cannot be put on today's scale)."""
    if first is None or last is None:
        return "EMPTY"
    if stored_last is not None and last < stored_last - timedelta(days=OVERLAP_DAYS):
        return "STALE_END"
    if (
        stored_first is not None
        and first > stored_first + timedelta(days=REFETCH_SHORT_START_DAYS)
        and now - mark <= timedelta(days=REFETCH_ACCEPT_AFTER_DAYS)
    ):
        return "SHORT_START"
    return None


def update_closes(
    engine: Engine,
    engines: BuiltEngines,
    now: datetime,
    targets: Sequence[tuple[UUID, UUID]] = (),
) -> ClosesResult:
    """Fetch the closes of every active listing of a linked instrument, of each target
    waiting to be back-filled and of each listing an `OK` check points to whose last answer
    was a mismatch: a marked listing first downloads its whole series (oldest mark first),
    the others from their last stored days, compared with the answer (D-122, D-201)."""
    result = ClosesResult()
    by_name = {p.name: p for c in PriceClass for p in engines.prices(c)}
    wanted = {listing_id for _, listing_id in targets}
    # A listing its engine still points to whose last answer was in another currency or unit:
    # fetched again, so the mismatch clears itself once the answers fit (D-119).
    confirmed = (
        select(InstrumentEngineCheck)
        .where(
            InstrumentEngineCheck.instrument_id == InstrumentListing.instrument_id,
            InstrumentEngineCheck.provider == InstrumentListing.provider,
            InstrumentEngineCheck.symbol == InstrumentListing.symbol,
            InstrumentEngineCheck.status == "OK",
        )
        .exists()
    )
    mismatched = (
        (InstrumentListing.status == "MISMATCH")
        & (InstrumentListing.mismatch != MismatchKind.LEVEL.value)
        & confirmed
    )
    with db_transaction(engine, write=False) as session:
        linked = {i.id for i in instruments.linked_instruments(session)}
        rows = session.scalars(
            select(InstrumentListing).where(
                InstrumentListing.is_active | InstrumentListing.id.in_(wanted) | mismatched
            )
        ).all()
        plan = [_to_fetch(session, row) for row in rows if row.instrument_id in linked]
    # Marked listings first, oldest mark first (a shutdown half-way leaves the newest marks).
    plan.sort(key=lambda item: (item.mark is None, item.mark or _EARLIEST))
    for item in plan:
        provider = by_name.get(item.provider)
        if provider is None:
            continue  # its engine is disabled: no call, the closes go stale, any mark stays
        if item.mark is not None:
            fall_back = _refetch(engine, provider, item, now, result, Refetch.MARK, item.mark)
            if not fall_back or item.last is None:
                continue
            # Refused or failed: the daily update still happens (never a frozen listing),
            # without a second full download; the mark stays for the next run.
        _update(engine, provider, item, now, result)
    return result


_EARLIEST = datetime.min.replace(tzinfo=UTC)


def _succeeded(listing: InstrumentListing, now: datetime) -> None:
    listing.status, listing.mismatch = "OK", None
    listing.last_success_at, listing.failure_count, listing.last_error_code = now, 0, None


def _mark(engine: Engine, listing_id: UUID, now: datetime) -> None:
    """Mark a listing to be downloaded again whole (D-201); an existing mark is never moved."""
    with db_transaction(engine, write=True) as session:
        listing = session.get(InstrumentListing, listing_id)
        if listing is not None and listing.refetch_requested_at is None:
            listing.refetch_requested_at = now


def _to_fetch(session, row: InstrumentListing) -> _ToFetch:
    overlap = instruments.overlap_start(session, row.id, REBASE_WINDOW)
    window = (
        {c.day: c.close for c in instruments.stored_closes(session, row.id, since=overlap)}
        if overlap is not None
        else {}
    )
    return _ToFetch(
        row.id,
        row.instrument_id,
        row.provider,
        row.symbol,
        row.currency,
        QuoteUnit(row.unit),
        overlap,
        window,
        instruments.first_stored_day(session, row.id),
        row.refetch_requested_at,
    )


_FAILED, _MISMATCH = "FAILED", "MISMATCH"


def _fetch(
    engine: Engine,
    provider: PriceProvider,
    item: _ToFetch,
    start: date,
    today: date,
    result: ClosesResult,
) -> list[StoredClose] | str:
    """One call for a listing's closes from `start`: the closes in the stored unit, or
    `_FAILED` after an error (the listing `FAILING`, its closes kept) or `_MISMATCH` for an
    answer in another currency or unit (the listing `MISMATCH`, nothing stored)."""
    try:
        series = provider.daily_closes(item.symbol, start, today)
    except Exception as error:  # an adapter bug must not stop the run (D-121)
        code = error_code(error)
        result.errors[code] += 1
        log.warning("closes fetch failed", extra={"provider": item.provider, "code": code})
        _closes_failed(engine, item, code)
        return _FAILED
    if (series.currency, series.unit) != (item.currency, item.unit):
        kind = MismatchKind.CURRENCY if series.currency != item.currency else MismatchKind.UNIT
        result.mismatch += 1
        log.warning("closes in another unit", extra={"provider": item.provider, "kind": kind})
        _set_listing(engine, item.id, status="MISMATCH", mismatch=kind.value)
        return _MISMATCH
    return [
        StoredClose(
            c.day,
            to_stored(c.close, series.unit),
            {"unit": series.unit.value} if series.unit is QuoteUnit.MINOR_CURRENCY_UNIT else None,
        )
        for c in series.closes
        if start <= c.day <= today
    ]


def _update(
    engine: Engine, provider: PriceProvider, item: _ToFetch, now: datetime, result: ClosesResult
) -> None:
    """The daily update of a listing from its last stored days, compared with the answer
    first: a rescaled series is not appended but marked and, if it was not marked yet,
    downloaded again whole in the same run, once (D-201)."""
    today = now.date()
    start = fetch_start(item.overlap, today, provider.capabilities.history_years)
    if start > today:
        return
    closes = _fetch(engine, provider, item, start, today, result)
    if isinstance(closes, str):
        return
    if item.last is not None and rebase_needed(
        item.window, {c.day: c.close for c in closes}, item.last
    ):
        log.warning("closes rescaled by the provider", extra={"provider": item.provider})
        if item.mark is None:  # otherwise its full download was just refused or failed
            result.rebased += 1
            _mark(engine, item.id, now)
            _refetch(engine, provider, item, now, result, Refetch.REBASE, now)
        return  # nothing of a rescaled answer is stored
    with db_transaction(engine, write=True) as session:
        listing = session.get(InstrumentListing, item.id)
        if listing is None:
            return  # deleted meanwhile
        result.stored += instruments.store_closes(session, item.id, closes)
        if closes:
            result.filled.add(item.id)
        _succeeded(listing, now)
        result.updated += 1


def _refetch(
    engine: Engine,
    provider: PriceProvider,
    item: _ToFetch,
    now: datetime,
    result: ClosesResult,
    reason: Refetch,
    mark: datetime,
) -> bool:
    """Download a listing's whole series again and replace its closes in one write, clearing
    the mark (D-201). The window starts at the earlier of its first stored day and the horizon,
    so a series never loses depth because the horizon moved. Returns True when the answer was
    refused or the call failed (the mark and the stored closes stay), so the caller can still do
    the daily update. A `MARK` keeps the stored days after the answer's last day unless the
    answer differs from the stored window; a `REBASE` replaces every day: kept days would stay
    on the old scale."""
    today = now.date()
    start = fetch_start(None, today, provider.capabilities.history_years)
    if item.first is not None:
        start = min(start, item.first)
    if start > today:
        return False
    closes = _fetch(engine, provider, item, start, today, result)
    if isinstance(closes, str):
        return closes == _FAILED  # another currency or unit: nothing to fall back to
    first, last = (closes[0].day, closes[-1].day) if closes else (None, None)
    refusal = refetch_refusal(first, last, item.first, item.last, mark, now)
    if refusal is not None:
        result.refetch_refused += 1
        log.warning("full closes refused", extra={"provider": item.provider, "reason": refusal})
        return True
    assert first is not None and last is not None
    if (
        reason is Refetch.MARK
        and item.last is not None
        and rebase_needed(item.window, {c.day: c.close for c in closes}, item.last)
    ):
        reason = Refetch.REBASE  # e.g. a mark left by an earlier run's refused rebase
    with db_transaction(engine, write=True) as session:
        listing = session.get(InstrumentListing, item.id)
        if listing is None:
            return False  # deleted meanwhile
        keep_after = last if reason is Refetch.MARK else None
        result.stored += instruments.replace_closes(session, item.id, closes, keep_after=keep_after)
        result.filled.add(item.id)
        listing.refetch_requested_at = None
        _succeeded(listing, now)
        result.updated += 1
        result.refetched += 1
        if item.first is not None and first > item.first + timedelta(days=REFETCH_SHORT_START_DAYS):
            result.shortened += 1  # accepted once the mark was old enough
    return False


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
    filled: Collection[UUID],
    now: datetime,
) -> SwitchResult:
    """Activate each target back-filled in this run (`filled`) in place of the active listing
    (make before break, D-134), after the level check when the active listing's engine is
    still enabled and its symbol still known to the provider."""
    result = SwitchResult()
    for instrument_id, target_id in targets:
        with db_transaction(engine, write=True) as session:
            instrument = session.get(Instrument, instrument_id)
            target = session.get(InstrumentListing, target_id)
            if instrument is None or target is None or target.is_active:
                continue
            active = instruments.active_listing(session, instrument_id)
            if target.id not in filled or not _recent_enough(session, target, active):
                result.waiting += 1  # not back-filled yet: the active listing stays
                continue
            if (
                active is not None
                and active.status != "NOT_FOUND"  # a dropped symbol is no reference
                and _still_trusted(engines, instrument, active.provider)
            ):
                ratio = level_ratio(session, active.id, target.id)
                if ratio is not None and abs(ratio - 1) > LEVEL_TOLERANCE:
                    _refuse_level(session, target, active, now)
                    result.level_refused += 1
                    log.warning(
                        "listing switch refused by the level check",
                        extra={"provider": target.provider},
                    )
                    continue
            instruments.activate(session, target, now)
            instruments.delete_other_listings(session, target)
            result.switched += 1
    return result


def _recent_enough(session, target: InstrumentListing, active: InstrumentListing | None) -> bool:
    """Whether the target's last close is at most `OVERLAP_DAYS` older than the active
    listing's: never switch to a series that stopped before the one it replaces."""
    last = instruments.last_stored_day(session, target.id)
    if last is None:
        return False
    reference = instruments.last_stored_day(session, active.id) if active else None
    return reference is None or last >= reference - timedelta(days=OVERLAP_DAYS)


def _refuse_level(
    session, target: InstrumentListing, active: InstrumentListing, now: datetime
) -> None:
    """The target is a `LEVEL` mismatch and loses its closes (a later acceptance back-fills it
    afresh). Another engine waits `RETRY_DAYS`; the active listing's own engine keeps its
    check on the active listing, which a refused replacement never takes away (D-134)."""
    target.status, target.mismatch = "MISMATCH", MismatchKind.LEVEL.value
    session.execute(delete(InstrumentPrice).where(InstrumentPrice.listing_id == target.id))
    if target.provider == active.provider and active.status in _USABLE:
        _confirm_active(session, active, now)
        return
    instruments.save_check(
        session,
        target.instrument_id,
        target.provider,
        status="MISMATCH",
        symbol=None,
        mismatch=MismatchKind.LEVEL.value,
        checked_at=now,
    )


def _still_trusted(engines: BuiltEngines, instrument: Instrument, provider: str) -> bool:
    """Whether the active listing's engine is still enabled in the instrument's order: then
    a switch is a reorder and needs the level check; otherwise the administrator has
    disabled it, or the class changed, and the switch happens without it (D-134)."""
    price_class = PriceClass(instrument.price_class) if instrument.price_class else None
    return any(p.name == provider for _, p in orders(engines, price_class))


def recheck_classes(
    engine: Engine, engines: BuiltEngines, counts: Counter[str], errors: Counter[str]
) -> dict[UUID, Facts]:
    """The re-check job's first step (D-133): a class inferred from a unit (`UNIT`) is asked of
    the classifiers again; a class answered replaces it with the engine's name. When the class
    changes, an active listing whose unit does not fit the new class is deleted with its
    closes (it would feed a series of the wrong kind) and its engine's check becomes a `UNIT`
    mismatch with no search date, so the engine is searched again under the new class; the
    walk then looks for a listing in the new order. Adds to `counts` (`reclassified` when the
    class changed, `confirmed` when it did not, `deactivated`) and `errors` instrument by
    instrument, so a run that stops half-way keeps them. Returns what the classifiers said per
    instrument, for the walk of the same run."""
    said: dict[UUID, Facts] = {}
    if not engines.classifiers():
        return said
    with db_transaction(engine, write=False) as session:
        inferred = [
            (i.id, i.isin)
            for i in instruments.linked_instruments(session)
            if i.class_source == instruments.CLASS_FROM_UNIT
        ]
    for instrument_id, isin in inferred:
        result = ResolveResult()
        facts = said[instrument_id] = classify(engines, isin, result.errors)
        errors.update(result.errors)
        if facts.price_class is None or facts.source is None:
            continue
        with db_transaction(engine, write=True) as session:
            instrument = session.get(Instrument, instrument_id)
            if instrument is None or instrument.class_source != instruments.CLASS_FROM_UNIT:
                continue
            changed = instrument.price_class != facts.price_class.value
            instruments.set_price_class(session, instrument, facts.price_class, facts.source)
            counts["reclassified" if changed else "confirmed"] += 1
            active = instruments.active_listing(session, instrument_id)
            if (
                changed
                and active is not None
                and not fits_class(facts.price_class, QuoteUnit(active.unit))
            ):
                instruments.save_check(
                    session,
                    instrument_id,
                    active.provider,
                    status="MISMATCH",
                    symbol=None,
                    mismatch=MismatchKind.UNIT.value,
                    checked_at=None,
                )
                session.execute(delete(InstrumentListing).where(InstrumentListing.id == active.id))
                counts["deactivated"] += 1
                log.warning(
                    "active listing does not fit the corrected class",
                    extra={"provider": active.provider},
                )
    return said


def refresh_prices(
    engine: Engine,
    engines: BuiltEngines,
    exchanges: Sequence[str],
    now: datetime,
    *,
    recheck: bool = False,
    classified: Mapping[UUID, Facts] | None = None,
) -> tuple[ResolveResult, ClosesResult, SwitchResult]:
    """One price run: listings, then closes (back-fill included), then switches."""
    resolved = resolve_listings(
        engine, engines, exchanges, now, recheck=recheck, classified=classified
    )
    closes = update_closes(engine, engines, now, resolved.targets)
    switched = switch_listings(engine, engines, resolved.targets, closes.filled, now)
    return resolved, closes, switched
