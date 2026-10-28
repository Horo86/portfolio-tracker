"""Instruments, listings, provider closes, FX rates, the engine configuration and the price
merge (D-116, D-119, D-122, D-132 to D-134, domain-rules §12). Invented ISINs (country "ZZ"),
symbols and prices only."""

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from helpers import fake_isin
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portfolio_tracker.db import models
from portfolio_tracker.engine.valuation import PriceHistory
from portfolio_tracker.marketdata.base import PriceClass, Rate, Role
from portfolio_tracker.marketdata.routing import RoutingConfig
from portfolio_tracker.marketdata.units import Mismatch, MismatchKind
from portfolio_tracker.repositories import assets, fx, instruments, market_config, prices, users
from portfolio_tracker.repositories.instruments import StoredClose

ISIN = fake_isin(41)


def user(session: Session, username: str) -> models.User:
    return users.add_user(
        session, username=username, role="INVESTOR", password_hash="x", language="en"
    )


def asset(
    session: Session,
    owner: models.User,
    *,
    isin: str | None = ISIN,
    currency: str = "EUR",
    unit: str = "CURRENCY",
    asset_type: str = "ETF",
) -> models.Asset:
    return assets.add_private_asset(
        session,
        owner.id,
        isin=isin,
        name="Example Fund",
        asset_type=asset_type,
        quote_currency=currency,
        quote_unit=unit,
    )


def active_listing(
    session: Session,
    instrument: models.Instrument,
    *,
    provider: str = "alpha",
    currency: str = "EUR",
    unit: str = "CURRENCY",
) -> models.InstrumentListing:
    listing = instruments.save_listing(
        session, instrument.id, provider, f"EXMPL.{provider.upper()}", currency=currency, unit=unit
    )
    instruments.activate(session, listing)
    return listing


def closes(session: Session, listing: models.InstrumentListing, **by_day: str) -> None:
    """`closes(s, l, d0302="9.80")` stores a close for 2 March 2026."""
    instruments.store_closes(
        session,
        listing.id,
        [StoredClose(date(2026, int(k[1:3]), int(k[3:5])), Decimal(v)) for k, v in by_day.items()],
    )


@pytest.fixture
def alice(db_session: Session) -> models.User:
    return user(db_session, "alice")


@pytest.fixture
def bob(db_session: Session) -> models.User:
    return user(db_session, "bob")


# --- instruments and listings ---------------------------------------------------------------


def test_one_instrument_per_isin_and_currency(db_session: Session) -> None:
    first = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    assert instruments.get_or_create_instrument(db_session, ISIN, "EUR").id == first.id
    other = instruments.get_or_create_instrument(db_session, ISIN, "GBP")
    assert other.id != first.id
    with pytest.raises(IntegrityError):
        db_session.add(models.Instrument(isin=ISIN, quote_currency="EUR"))
        db_session.flush()


def test_one_active_listing_per_instrument(db_session: Session) -> None:
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    first = active_listing(db_session, instrument, provider="alpha")
    second = active_listing(db_session, instrument, provider="beta")
    db_session.refresh(first)
    assert (first.is_active, second.is_active) == (False, True)
    assert instruments.active_listing(db_session, instrument.id).id == second.id
    with pytest.raises(IntegrityError):
        first.is_active = True  # bypassing activate(): the partial unique index refuses it
        db_session.flush()


@pytest.mark.parametrize(
    "fields",
    [
        {"symbol": None, "currency": "EUR", "unit": "CURRENCY"},
        {"symbol": "EXMPL.DE", "currency": None, "unit": "CURRENCY"},
        {"symbol": "EXMPL.DE", "currency": "EUR", "unit": None},
    ],
)
def test_a_listing_has_a_symbol_currency_and_unit(db_session: Session, fields: dict) -> None:
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    with pytest.raises(IntegrityError):
        db_session.add(
            models.InstrumentListing(instrument_id=instrument.id, provider="a", **fields)
        )
        db_session.flush()


def test_an_engine_may_hold_several_listings_but_each_symbol_once(db_session: Session) -> None:
    """A replacement within one engine is stored next to the active listing (D-134)."""
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    fields = {"currency": "EUR", "unit": "CURRENCY"}
    milan = instruments.save_listing(db_session, instrument.id, "alpha", "EXMPL.MI", **fields)
    xetra = instruments.save_listing(db_session, instrument.id, "alpha", "EXMPL.DE", **fields)
    again = instruments.save_listing(
        db_session, instrument.id, "alpha", "EXMPL.MI", exchange_code="MIL", **fields
    )
    assert again.id == milan.id and again.exchange_code == "MIL"
    assert [x.id for x in instruments.listings(db_session, instrument.id)] == [xetra.id, milan.id]
    assert (milan.status, milan.failure_count) == ("OK", 0)
    with pytest.raises(IntegrityError):
        db_session.add(
            models.InstrumentListing(
                instrument_id=instrument.id, provider="alpha", symbol="EXMPL.MI", **fields
            )
        )
        db_session.flush()


def test_one_check_per_instrument_and_engine(db_session: Session) -> None:
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    created = instruments.save_check(db_session, instrument.id, "alpha", status="NOT_FOUND")
    updated = instruments.save_check(
        db_session, instrument.id, "alpha", status="FAILING", failure_count=2
    )
    instruments.save_check(db_session, instrument.id, "beta", status="MISMATCH", mismatch="UNIT")
    assert updated is created
    assert (updated.status, updated.failure_count, updated.checked_at) == ("FAILING", 2, None)
    assert [c.provider for c in instruments.checks(db_session, instrument.id)] == ["alpha", "beta"]
    with pytest.raises(IntegrityError):
        db_session.execute(
            insert(models.InstrumentEngineCheck).values(
                instrument_id=instrument.id, provider="alpha", status="NOT_FOUND", failure_count=0
            )
        )


@pytest.mark.parametrize(
    ("status", "mismatch"), [("MISMATCH", None), ("FAILING", "UNIT"), ("NOT_FOUND", "LEVEL")]
)
def test_a_check_has_a_mismatch_kind_exactly_when_it_is_a_mismatch(
    db_session: Session, status: str, mismatch: str | None
) -> None:
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    with pytest.raises(IntegrityError):
        instruments.save_check(db_session, instrument.id, "a", status=status, mismatch=mismatch)


def test_closes_are_upserted_and_the_last_day_is_known(db_session: Session) -> None:
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    listing = active_listing(db_session, instrument)
    assert instruments.last_stored_day(db_session, listing.id) is None

    closes(db_session, listing, d0302="9.80", d0303="9.90")
    closes(db_session, listing, d0303="9.95", d0305="10.40")  # a corrected close and a new one

    stored = instruments.stored_closes(db_session, listing.id)
    assert [(p.day.day, p.close) for p in stored] == [
        (2, Decimal("9.80")),
        (3, Decimal("9.95")),
        (5, Decimal("10.40")),
    ]
    assert instruments.last_stored_day(db_session, listing.id) == date(2026, 3, 5)


def test_the_overlap_starts_at_the_fifth_last_stored_day(db_session: Session) -> None:
    """Stored days, not calendar days: a weekend or a gap never shortens it (D-122)."""
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    listing = active_listing(db_session, instrument)
    assert instruments.overlap_start(db_session, listing.id, 5) is None
    closes(db_session, listing, d0305="10", d0306="10")
    assert instruments.overlap_start(db_session, listing.id, 5) == date(2026, 3, 5)  # all
    closes(db_session, listing, d0226="10", d0227="10", d0302="10", d0303="10")
    assert instruments.overlap_start(db_session, listing.id, 5) == date(2026, 2, 27)


def test_close_metadata_keeps_the_original_unit(db_session: Session) -> None:
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "GBP")
    listing = active_listing(db_session, instrument, currency="GBP", unit="MINOR_CURRENCY_UNIT")
    meta = {"unit": "MINOR_CURRENCY_UNIT", "close": "1234.5"}
    instruments.store_closes(
        db_session, listing.id, [StoredClose(date(2026, 3, 2), Decimal("12.345"), meta)]
    )
    assert instruments.stored_closes(db_session, listing.id)[0].meta == meta


def test_many_closes_are_written_in_chunks(db_session: Session) -> None:
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    listing = active_listing(db_session, instrument)
    rows = [
        StoredClose(date.fromordinal(date(2006, 1, 1).toordinal() + i), Decimal(i))
        for i in range(5200)
    ]
    assert instruments.store_closes(db_session, listing.id, rows) == 5200
    assert len(instruments.stored_closes(db_session, listing.id)) == 5200


def test_linking_and_the_instruments_still_needed(db_session: Session, alice, bob) -> None:
    mine, theirs = asset(db_session, alice), asset(db_session, bob)
    no_isin = asset(db_session, alice, isin=None)
    assert {a.id for a in instruments.assets_to_link(db_session)} == {mine.id, theirs.id}
    assert no_isin.id not in {a.id for a in instruments.assets_to_link(db_session)}

    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    unused = instruments.get_or_create_instrument(db_session, fake_isin(42), "EUR")
    instruments.link_asset(db_session, mine.id, instrument.id)
    instruments.link_asset(db_session, theirs.id, instrument.id)

    assert instruments.assets_to_link(db_session) == []
    needed = instruments.linked_instruments(db_session)
    assert [i.id for i in needed] == [instrument.id]
    assert unused.id not in {i.id for i in needed}


def test_deleting_an_instrument_unlinks_its_assets(db_session: Session, alice) -> None:
    mine = asset(db_session, alice)
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    listing = active_listing(db_session, instrument)
    closes(db_session, listing, d0302="9.80")
    instruments.link_asset(db_session, mine.id, instrument.id)

    instruments.save_check(db_session, instrument.id, "alpha", status="OK", symbol="EXMPL.ALPHA")

    db_session.delete(instrument)
    db_session.flush()
    db_session.expire_all()

    assert db_session.get(models.Asset, mine.id).instrument_id is None
    assert db_session.scalars(select(models.InstrumentPrice)).all() == []
    assert db_session.scalars(select(models.InstrumentListing)).all() == []
    assert db_session.scalars(select(models.InstrumentEngineCheck)).all() == []


# --- the price merge (domain-rules §12, D-116) ----------------------------------------------


def linked(db_session: Session, owner: models.User, **kwargs: str) -> models.Asset:
    row = asset(db_session, owner, **kwargs)
    instrument = instruments.get_or_create_instrument(db_session, row.isin, row.quote_currency)
    instruments.link_asset(db_session, row.id, instrument.id)
    db_session.refresh(row)
    return row


def test_section_12_example_manual_and_provider_by_date(db_session: Session, alice) -> None:
    """manual 10.00 on 3 March; provider 9.80 on 2 March and 10.40 on 5 March: 3 and 4 March
    are valued at 10.00 (manual), 5 March at 10.40 (the newer provider close)."""
    mine = linked(db_session, alice)
    listing = active_listing(db_session, db_session.get(models.Instrument, mine.instrument_id))
    closes(db_session, listing, d0302="9.80", d0305="10.40")
    prices.upsert_manual_price(db_session, alice.id, mine.id, date(2026, 3, 3), Decimal("10.00"))

    history = PriceHistory(prices.closes_for(db_session, alice.id, {mine.id})[mine.id])

    assert history.on_or_before(date(2026, 3, 2)) == (date(2026, 3, 2), Decimal("9.80"))
    assert history.on_or_before(date(2026, 3, 3)) == (date(2026, 3, 3), Decimal("10.00"))
    assert history.on_or_before(date(2026, 3, 4)) == (date(2026, 3, 3), Decimal("10.00"))
    assert history.on_or_before(date(2026, 3, 5)) == (date(2026, 3, 5), Decimal("10.40"))


def test_a_manual_price_wins_on_its_own_day(db_session: Session, alice) -> None:
    mine = linked(db_session, alice)
    listing = active_listing(db_session, db_session.get(models.Instrument, mine.instrument_id))
    closes(db_session, listing, d0303="9.90")
    prices.upsert_manual_price(db_session, alice.id, mine.id, date(2026, 3, 3), Decimal("10.00"))

    series = prices.price_series(db_session, alice.id, {mine.id})[mine.id]
    assert series.closes == {date(2026, 3, 3): Decimal("10.00")}
    assert series.manual_days == {date(2026, 3, 3)}
    assert series.mismatch is None


def test_an_old_manual_price_never_hides_newer_provider_closes(db_session, alice) -> None:
    mine = linked(db_session, alice)
    listing = active_listing(db_session, db_session.get(models.Instrument, mine.instrument_id))
    prices.upsert_manual_price(db_session, alice.id, mine.id, date(2025, 12, 31), Decimal("8"))
    closes(db_session, listing, d0302="9.80")

    history = PriceHistory(prices.closes_for(db_session, alice.id, {mine.id})[mine.id])
    assert history.on_or_before(date(2026, 3, 10)) == (date(2026, 3, 2), Decimal("9.80"))
    assert history.on_or_before(date(2026, 1, 15)) == (date(2025, 12, 31), Decimal("8"))


def test_one_download_serves_every_linked_asset_and_manual_prices_stay_private(
    db_session: Session, alice, bob
) -> None:
    mine, theirs = linked(db_session, alice), linked(db_session, bob)
    assert mine.instrument_id == theirs.instrument_id
    listing = active_listing(db_session, db_session.get(models.Instrument, mine.instrument_id))
    closes(db_session, listing, d0302="9.80", d0303="9.90")
    prices.upsert_manual_price(db_session, alice.id, mine.id, date(2026, 3, 3), Decimal("12.50"))

    assert prices.closes_for(db_session, alice.id, {mine.id}) == {
        mine.id: {date(2026, 3, 2): Decimal("9.80"), date(2026, 3, 3): Decimal("12.50")}
    }
    assert prices.closes_for(db_session, bob.id, {theirs.id}) == {
        theirs.id: {date(2026, 3, 2): Decimal("9.80"), date(2026, 3, 3): Decimal("9.90")}
    }
    # Bob asking for Alice's asset (never done by the services) still reads no manual price.
    assert prices.price_series(db_session, bob.id, {mine.id})[mine.id].manual_days == set()
    assert prices.closes_for(db_session, bob.id, set()) == {}


def test_pounds_and_pence_assets_read_one_listing_exactly(db_session, alice, bob) -> None:
    pounds = linked(db_session, alice, currency="GBP", unit="CURRENCY", asset_type="STOCK")
    pence = linked(db_session, bob, currency="GBP", unit="MINOR_CURRENCY_UNIT", asset_type="STOCK")
    instrument = db_session.get(models.Instrument, pounds.instrument_id)
    listing = active_listing(db_session, instrument, currency="GBP", unit="MINOR_CURRENCY_UNIT")
    closes(db_session, listing, d0302="12.345")  # stored in pounds

    assert prices.closes_for(db_session, alice.id, {pounds.id})[pounds.id] == {
        date(2026, 3, 2): Decimal("12.345")
    }
    in_pence = prices.closes_for(db_session, bob.id, {pence.id})[pence.id][date(2026, 3, 2)]
    assert in_pence == Decimal("1234.5") and str(in_pence) == "1234.5"


def test_an_asset_whose_unit_does_not_fit_reads_no_provider_close(db_session, alice, bob) -> None:
    bond = linked(db_session, alice, unit="PERCENT_OF_NOMINAL", asset_type="BOND")
    fund = linked(db_session, bob)  # same ISIN and currency, quoted per unit
    listing = active_listing(db_session, db_session.get(models.Instrument, bond.instrument_id))
    closes(db_session, listing, d0302="101.5")
    prices.upsert_manual_price(db_session, alice.id, bond.id, date(2026, 3, 1), Decimal("99.10"))

    series = prices.price_series(db_session, alice.id, {bond.id})[bond.id]
    assert series.closes == {date(2026, 3, 1): Decimal("99.10")}  # manual prices still work
    assert series.mismatch == Mismatch(
        MismatchKind.UNIT, asset="EUR PERCENT_OF_NOMINAL", listing="EUR CURRENCY"
    )
    other = prices.price_series(db_session, bob.id, {fund.id})[fund.id]
    assert other.mismatch is None and other.closes == {date(2026, 3, 2): Decimal("101.5")}


def test_only_the_active_listing_is_read(db_session: Session, alice) -> None:
    mine = linked(db_session, alice)
    instrument = db_session.get(models.Instrument, mine.instrument_id)
    old = active_listing(db_session, instrument, provider="alpha")
    closes(db_session, old, d0302="1.00")
    new = active_listing(db_session, instrument, provider="beta")
    closes(db_session, new, d0303="2.00")

    assert prices.closes_for(db_session, alice.id, {mine.id})[mine.id] == {
        date(2026, 3, 3): Decimal("2.00")
    }


def test_an_unlinked_asset_has_manual_prices_only(db_session: Session, alice) -> None:
    mine = asset(db_session, alice, isin=None)
    prices.upsert_manual_price(db_session, alice.id, mine.id, date(2026, 3, 1), Decimal("5"))
    series = prices.price_series(db_session, alice.id, {mine.id})[mine.id]
    assert series.closes == {date(2026, 3, 1): Decimal("5")}
    assert series.mismatch is None


def test_legacy_provider_rows_of_the_prices_table_are_not_read(db_session, alice) -> None:
    mine = asset(db_session, alice)
    db_session.add(
        models.Price(asset_id=mine.id, day=date(2026, 3, 2), close=Decimal(7), source="PROVIDER")
    )
    db_session.flush()
    assert prices.closes_for(db_session, alice.id, {mine.id}) == {mine.id: {}}


# --- the latest price on a day (domain-rules §12, D-116, D-154) -----------------------------


def latest(db_session: Session, owner: models.User, row: models.Asset, day: str):
    """`latest(s, alice, mine, "0304")`: the price of `row` on 4 March 2026 for `owner`."""
    on = date(2026, int(day[:2]), int(day[2:]))
    return prices.latest_prices(db_session, owner.id, {row.id}, on)[row.id]


def test_section_12_example_latest_price_on_each_day(db_session: Session, alice) -> None:
    mine = linked(db_session, alice)
    listing = active_listing(db_session, db_session.get(models.Instrument, mine.instrument_id))
    listing.exchange = "Milan"
    closes(db_session, listing, d0302="9.80", d0305="10.40")
    prices.upsert_manual_price(db_session, alice.id, mine.id, date(2026, 3, 3), Decimal("10.00"))

    expected = {
        "0301": (None, False),
        "0302": ((date(2026, 3, 2), Decimal("9.80")), False),
        "0303": ((date(2026, 3, 3), Decimal("10.00")), True),
        "0304": ((date(2026, 3, 3), Decimal("10.00")), True),
        "0305": ((date(2026, 3, 5), Decimal("10.40")), False),
    }
    for day, (price, manual) in expected.items():
        found = latest(db_session, alice, mine, day)
        assert (found.price, found.manual) == (price, manual), day
        assert (found.exchange, found.mismatch) == ("Milan", None)


def test_the_latest_manual_price_wins_a_tie_with_the_provider(db_session, alice) -> None:
    mine = linked(db_session, alice)
    listing = active_listing(db_session, db_session.get(models.Instrument, mine.instrument_id))
    closes(db_session, listing, d0302="9.80", d0303="9.90")
    prices.upsert_manual_price(db_session, alice.id, mine.id, date(2026, 3, 3), Decimal("10.00"))

    found = latest(db_session, alice, mine, "0310")
    assert (found.price, found.manual) == ((date(2026, 3, 3), Decimal("10.00")), True)


def test_prices_after_the_day_are_never_used(db_session: Session, alice) -> None:
    """A manual price may be dated tomorrow (D-149): it counts only from its own day."""
    mine = linked(db_session, alice)
    listing = active_listing(db_session, db_session.get(models.Instrument, mine.instrument_id))
    closes(db_session, listing, d0302="9.80", d0305="10.40")
    prices.upsert_manual_price(db_session, alice.id, mine.id, date(2026, 3, 6), Decimal("11"))

    assert latest(db_session, alice, mine, "0304").price == (date(2026, 3, 2), Decimal("9.80"))
    assert latest(db_session, alice, mine, "0305").price == (date(2026, 3, 5), Decimal("10.40"))
    assert latest(db_session, alice, mine, "0306").price == (date(2026, 3, 6), Decimal("11"))


def test_no_price_on_or_before_the_day(db_session: Session, alice) -> None:
    """Valued at cost by the engine (D-46): no price, nothing manual."""
    mine = linked(db_session, alice)
    listing = active_listing(db_session, db_session.get(models.Instrument, mine.instrument_id))
    closes(db_session, listing, d0305="10.40")
    unpriced = asset(db_session, alice, isin=None)

    found = latest(db_session, alice, mine, "0304")
    assert (found.price, found.manual, found.mismatch) == (None, False, None)
    assert latest(db_session, alice, unpriced, "0304").price is None


def test_the_latest_price_reads_only_the_active_listing(db_session: Session, alice) -> None:
    mine = linked(db_session, alice)
    instrument = db_session.get(models.Instrument, mine.instrument_id)
    old = active_listing(db_session, instrument, provider="alpha")
    closes(db_session, old, d0305="1.00")  # newer, but no longer active (D-134)
    new = active_listing(db_session, instrument, provider="beta")
    closes(db_session, new, d0303="2.00")

    assert latest(db_session, alice, mine, "0310").price == (date(2026, 3, 3), Decimal("2.00"))


def test_the_latest_price_in_pence_and_pounds(db_session: Session, alice, bob) -> None:
    pounds = linked(db_session, alice, currency="GBP", unit="CURRENCY", asset_type="STOCK")
    pence = linked(db_session, bob, currency="GBP", unit="MINOR_CURRENCY_UNIT", asset_type="STOCK")
    instrument = db_session.get(models.Instrument, pounds.instrument_id)
    listing = active_listing(db_session, instrument, currency="GBP", unit="MINOR_CURRENCY_UNIT")
    closes(db_session, listing, d0302="12.345")  # stored in pounds

    assert latest(db_session, alice, pounds, "0302").price == (date(2026, 3, 2), Decimal("12.345"))
    in_pence = latest(db_session, bob, pence, "0302").price
    assert in_pence == (date(2026, 3, 2), Decimal("1234.5")) and str(in_pence[1]) == "1234.5"


def test_a_mismatched_asset_has_its_manual_latest_price_only(db_session, alice) -> None:
    bond = linked(db_session, alice, unit="PERCENT_OF_NOMINAL", asset_type="BOND")
    listing = active_listing(db_session, db_session.get(models.Instrument, bond.instrument_id))
    closes(db_session, listing, d0302="101.5")
    prices.upsert_manual_price(db_session, alice.id, bond.id, date(2026, 3, 1), Decimal("99.10"))

    found = latest(db_session, alice, bond, "0310")
    assert (found.price, found.manual) == ((date(2026, 3, 1), Decimal("99.10")), True)
    assert found.mismatch == Mismatch(
        MismatchKind.UNIT, asset="EUR PERCENT_OF_NOMINAL", listing="EUR CURRENCY"
    )
    assert found.exchange is None


def test_the_latest_price_is_the_owners_only(db_session: Session, alice, bob) -> None:
    mine, theirs = linked(db_session, alice), linked(db_session, bob)
    listing = active_listing(db_session, db_session.get(models.Instrument, mine.instrument_id))
    closes(db_session, listing, d0302="9.80")
    prices.upsert_manual_price(db_session, alice.id, mine.id, date(2026, 3, 3), Decimal("12.50"))

    assert latest(db_session, bob, theirs, "0310").price == (date(2026, 3, 2), Decimal("9.80"))
    # Bob asking for Alice's asset (never done by the services) reads nothing of hers.
    assert latest(db_session, bob, mine, "0310").price is None
    assert prices.latest_prices(db_session, bob.id, set(), date(2026, 3, 10)) == {}


def test_the_latest_price_ignores_a_stale_link_and_legacy_rows(db_session, alice) -> None:
    mine = linked(db_session, alice)
    listing = active_listing(db_session, db_session.get(models.Instrument, mine.instrument_id))
    closes(db_session, listing, d0302="9.80")
    mine.isin = fake_isin(43)
    db_session.add(
        models.Price(asset_id=mine.id, day=date(2026, 3, 3), close=Decimal(7), source="PROVIDER")
    )
    db_session.flush()
    assert latest(db_session, alice, mine, "0310").price is None


def test_the_latest_price_equals_the_full_series_on_every_day(db_session, alice, bob) -> None:
    """D-154 (b): the latest price is exactly what the merged full series (`price_series`)
    gives on that day, with its source, mismatch and exchange. Invented closes on random days
    (fixed seed), manual prices on some of the same days, between them and after them."""
    rng = random.Random(154)
    start = date(2026, 3, 1)
    eur = linked(db_session, alice)
    pence = linked(
        db_session, alice, isin=fake_isin(44), currency="GBP", unit="MINOR_CURRENCY_UNIT"
    )
    bond = linked(
        db_session, alice, isin=fake_isin(45), unit="PERCENT_OF_NOMINAL", asset_type="BOND"
    )
    unlinked = asset(db_session, alice, isin=None)
    stale = linked(db_session, alice, isin=fake_isin(46))
    theirs = linked(db_session, bob)  # reads the same listing as `eur`, with Bob's own prices
    provider_days: dict[UUID, list[date]] = {}
    for row, currency in ((eur, "EUR"), (pence, "GBP"), (bond, "EUR"), (stale, "EUR")):
        instrument = db_session.get(models.Instrument, row.instrument_id)
        listing = active_listing(db_session, instrument, currency=currency)
        listing.exchange = f"Exchange {row.isin}"
        days = sorted(start + timedelta(d) for d in rng.sample(range(5, 60), 25))
        provider_days[row.id] = days
        stored = [StoredClose(d, Decimal(rng.randint(100, 9999)) / 100) for d in days]
        instruments.store_closes(db_session, listing.id, stored)
    stale.quote_currency = "USD"  # no longer its instrument's currency: never fed
    owned = {alice: [eur, pence, bond, unlinked, stale], bob: [theirs]}
    for owner, rows in owned.items():
        for row in rows:
            days = {start + timedelta(d) for d in rng.sample(range(0, 70), 8)}
            days.add(provider_days[eur.id][3])  # a tie wherever `eur`'s listing feeds it
            for day in days:
                close = Decimal(rng.randint(100, 9999)) / 100
                prices.upsert_manual_price(db_session, owner.id, row.id, day, close)

    sources: set[bool] = set()
    for owner, rows in owned.items():
        ids = {row.id for row in rows}
        series = prices.price_series(db_session, owner.id, ids)
        for offset in range(-2, 72):
            day = start + timedelta(offset)
            found = prices.latest_prices(db_session, owner.id, ids, day)
            assert set(found) == ids
            for asset_id in ids:
                full, got = series[asset_id], found[asset_id]
                expected = PriceHistory(full.closes).on_or_before(day)
                assert got.price == expected, (asset_id, day)
                assert got.manual == (expected is not None and expected[0] in full.manual_days)
                assert (got.mismatch, got.exchange) == (full.mismatch, full.exchange)
                if expected is not None:
                    sources.add(got.manual)
    assert sources == {True, False}  # both sources were exercised


# --- FX rates -----------------------------------------------------------------------------


def test_the_fx_overlap_starts_at_the_fifth_last_stored_day(db_session: Session) -> None:
    days = [date(2026, 2, 26), date(2026, 2, 27), date(2026, 3, 2), date(2026, 3, 3)]
    fx.store_rates(db_session, [Rate(d, "USD", Decimal("1.08")) for d in days], "frankfurter")
    fx.store_rates(db_session, [Rate(date(2026, 3, 4), "GBP", Decimal("0.85"))], "frankfurter")
    assert fx.overlap_start(db_session, "USD", 5) == date(2026, 2, 26)  # fewer: all of them
    fx.store_rates(db_session, [Rate(date(2026, 3, 9), "USD", Decimal("1.08"))], "frankfurter")
    fx.store_rates(db_session, [Rate(date(2026, 3, 10), "USD", Decimal("1.08"))], "frankfurter")
    assert fx.overlap_start(db_session, "USD", 5) == date(2026, 2, 27)
    assert fx.overlap_start(db_session, "CHF", 5) is None


def test_fx_rates_are_upserted_and_read_by_currency(db_session: Session) -> None:
    d1, d2 = date(2026, 3, 2), date(2026, 3, 3)
    assert fx.last_stored_day(db_session, "USD") is None
    fx.store_rates(
        db_session,
        [Rate(d1, "USD", Decimal("1.0812")), Rate(d1, "GBP", Decimal("0.8571"))],
        "frankfurter",
    )
    fx.store_rates(
        db_session,
        [Rate(d1, "USD", Decimal("1.0815")), Rate(d2, "USD", Decimal("1.0790"))],
        "frankfurter",
    )

    assert fx.last_stored_day(db_session, "USD") == d2
    assert fx.last_stored_day(db_session, "GBP") == d1
    assert fx.rates_for(db_session, {"USD", "CHF"}) == {
        "USD": {d1: Decimal("1.0815"), d2: Decimal("1.0790")},
        "CHF": {},
    }
    assert fx.rates_for(db_session, set()) == {}


def test_a_stale_link_never_feeds_an_asset(db_session: Session, alice) -> None:
    """Defence in depth: a link to an instrument of another ISIN or currency is ignored."""
    mine = linked(db_session, alice)
    listing = active_listing(db_session, db_session.get(models.Instrument, mine.instrument_id))
    closes(db_session, listing, d0302="9.80")
    for field, value in (("isin", fake_isin(43)), ("isin", None), ("quote_currency", "USD")):
        original = getattr(mine, field)
        setattr(mine, field, value)
        db_session.flush()
        assert prices.closes_for(db_session, alice.id, {mine.id}) == {mine.id: {}}
        setattr(mine, field, original)
        db_session.flush()
    assert prices.closes_for(db_session, alice.id, {mine.id})[mine.id]


def test_provider_closes_are_read_only_for_the_owners_assets(db_session, alice, bob) -> None:
    mine = linked(db_session, alice)
    listing = active_listing(db_session, db_session.get(models.Instrument, mine.instrument_id))
    closes(db_session, listing, d0302="9.80")
    assert prices.closes_for(db_session, bob.id, {mine.id}) == {mine.id: {}}


# --- price class, activation date, counts (D-133 to D-135) ---------------------------------


def test_the_price_class_records_its_source(db_session: Session) -> None:
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    assert (instrument.price_class, instrument.class_source) == (None, None)
    instruments.set_price_class(
        db_session, instrument, PriceClass.BOND, instruments.CLASS_FROM_UNIT
    )
    db_session.expire(instrument)
    assert (instrument.price_class, instrument.class_source) == ("BOND", "UNIT")


def test_activation_is_dated(db_session: Session) -> None:
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    first = active_listing(db_session, instrument, provider="alpha")
    at = datetime(2026, 3, 2, 22, 0, tzinfo=UTC)
    fields = {"currency": "EUR", "unit": "CURRENCY"}
    second = instruments.save_listing(db_session, instrument.id, "beta", "EXMPL.B", **fields)
    instruments.activate(db_session, second, at)
    db_session.refresh(first)
    assert first.activated_at is not None and not first.is_active  # its last activation stays
    assert second.activated_at == at


def test_check_counts_are_per_engine_and_status(db_session: Session) -> None:
    for number, (provider, status) in enumerate(
        [("alpha", "OK"), ("alpha", "OK"), ("alpha", "NOT_FOUND"), ("beta", "OK")]
    ):
        instrument = instruments.get_or_create_instrument(db_session, fake_isin(50 + number), "EUR")
        instruments.save_check(db_session, instrument.id, provider, status=status, symbol="X")
    extra = instruments.get_or_create_instrument(db_session, fake_isin(50), "EUR")
    for symbol in ("EXMPL.MI", "EXMPL.DE"):  # listings do not count: one engine, one instrument
        instruments.save_listing(
            db_session, extra.id, "alpha", symbol, currency="EUR", unit="CURRENCY"
        )
    assert instruments.check_counts(db_session) == {
        ("alpha", "OK"): 2,
        ("alpha", "NOT_FOUND"): 1,
        ("beta", "OK"): 1,
    }


# --- stored engine configuration (D-132) ----------------------------------------------------

CONFIG = RoutingConfig(
    frozenset({"alpha", "fx-test"}),
    {
        Role.ETF_STOCK: ("alpha", "beta"),  # beta is off but keeps its place
        Role.FX: ("fx-test",),
        Role.CLASSIFY: ("alpha",),
    },
    ("MIL", "xetra"),
)


def test_nothing_is_stored_until_seeded(db_session: Session) -> None:
    assert market_config.load(db_session) is None
    assert market_config.resolve_requested_at(db_session) is None
    with pytest.raises(LookupError):
        market_config.request_resolve(db_session)


def test_seeding_stores_the_configuration_once(db_session: Session) -> None:
    assert market_config.seed_if_empty(db_session, CONFIG) is True
    assert market_config.load(db_session) == CONFIG
    other = RoutingConfig(frozenset({"beta"}), {Role.ETF_STOCK: ("beta",)})
    assert market_config.seed_if_empty(db_session, other) is False  # the database wins
    assert market_config.load(db_session) == CONFIG


def test_saving_replaces_the_whole_configuration(db_session: Session) -> None:
    market_config.save(db_session, CONFIG)
    changed = RoutingConfig(
        frozenset({"beta"}), {Role.ETF_STOCK: ("beta", "alpha"), Role.BOND: ("gamma",)}
    )
    market_config.save(db_session, changed)
    assert market_config.load(db_session) == changed
    settings = db_session.scalars(
        select(models.ProviderSetting).order_by(models.ProviderSetting.name)
    )
    assert [(s.name, s.enabled) for s in settings] == [
        ("alpha", False),
        ("beta", True),
        ("gamma", False),
    ]


def test_a_resolve_request_made_during_a_recheck_stays_pending(db_session: Session) -> None:
    """Compared with the value the re-check read, never with a clock: a request from a process
    whose clock is behind is not mistaken for the one handled."""
    market_config.save(db_session, CONFIG)
    asked = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
    market_config.request_resolve(db_session, asked)
    read = market_config.resolve_requested_at(db_session)
    behind = asked - timedelta(hours=1)
    market_config.request_resolve(db_session, behind)  # during the re-check
    assert market_config.clear_resolve_request(db_session, read) is False
    assert market_config.resolve_requested_at(db_session) == behind
    assert market_config.clear_resolve_request(db_session, behind) is True
    assert market_config.resolve_requested_at(db_session) is None


def test_unfinished_re_checks_are_counted_per_request(db_session: Session) -> None:
    market_config.save(db_session, CONFIG)
    asked = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
    market_config.request_resolve(db_session, asked)
    assert market_config.count_unfinished_resolve(db_session, asked) == 1
    assert market_config.count_unfinished_resolve(db_session, asked) == 2
    again = asked + timedelta(days=1)
    market_config.request_resolve(db_session, again)  # a new request starts afresh
    assert market_config.count_unfinished_resolve(db_session, asked) is None
    assert market_config.count_unfinished_resolve(db_session, again) == 1
    market_config.clear_resolve_request(db_session, again)
    assert db_session.get(models.MarketDataConfig, 1).resolve_attempts == 0


def test_saving_keeps_a_pending_resolve_request(db_session: Session) -> None:
    market_config.save(db_session, CONFIG)
    asked = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
    market_config.request_resolve(db_session, asked)
    market_config.save(db_session, CONFIG)
    assert market_config.resolve_requested_at(db_session) == asked
