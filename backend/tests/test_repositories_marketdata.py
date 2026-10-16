"""Instruments, listings, provider closes, FX rates and the price merge (D-116, D-119,
D-122, domain-rules §12). Invented ISINs (country "ZZ"), symbols and prices only."""

from datetime import date
from decimal import Decimal

import pytest
from helpers import fake_isin
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portfolio_tracker.db import models
from portfolio_tracker.engine.valuation import PriceHistory
from portfolio_tracker.marketdata.base import Rate
from portfolio_tracker.marketdata.units import Mismatch, MismatchKind
from portfolio_tracker.repositories import assets, fx, instruments, prices, users
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
        session,
        instrument.id,
        provider,
        symbol=f"EXMPL.{provider.upper()}",
        currency=currency,
        unit=unit,
        status="OK",
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
        {"currency": "EUR", "unit": "CURRENCY"},
        {"symbol": "EXMPL.DE", "unit": "CURRENCY"},
        {"symbol": "EXMPL.DE", "currency": "EUR"},
    ],
)
def test_an_incomplete_listing_cannot_be_active(db_session: Session, fields: dict) -> None:
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    with pytest.raises(IntegrityError):
        instruments.save_listing(
            db_session, instrument.id, "alpha", status="OK", is_active=True, **fields
        )


def test_save_listing_updates_the_providers_row(db_session: Session) -> None:
    instrument = instruments.get_or_create_instrument(db_session, ISIN, "EUR")
    created = instruments.save_listing(db_session, instrument.id, "alpha", status="NOT_FOUND")
    updated = instruments.save_listing(
        db_session, instrument.id, "alpha", status="FAILING", failure_count=2
    )
    assert updated.id == created.id
    assert (updated.status, updated.failure_count) == ("FAILING", 2)
    assert instruments.listings(db_session, instrument.id) == [updated]


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

    db_session.delete(instrument)
    db_session.flush()
    db_session.expire_all()

    assert db_session.get(models.Asset, mine.id).instrument_id is None
    assert db_session.scalars(select(models.InstrumentPrice)).all() == []


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


# --- FX rates -----------------------------------------------------------------------------


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
