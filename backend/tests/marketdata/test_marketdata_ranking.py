"""The rank of an engine's candidates for one instrument (D-125, D-136, D-152): preferred
exchanges, then the engine's main exchanges trading in the instrument's currency, then its
other main exchanges, then other venues; within an exchange, ISIN before hint, shorter symbols
first. Exchange codes and names here are Yahoo-like; symbols are invented."""

from portfolio_tracker.marketdata.base import MatchSource
from portfolio_tracker.marketdata.ranking import candidate_rank

MAIN = ("MIL", "GER", "LSE", "EBS", "NMS")
CURRENCIES = {"MIL": "EUR", "GER": "EUR", "LSE": "GBP", "EBS": "CHF", "NMS": "USD"}
ISIN, HINT = MatchSource.ISIN, MatchSource.HINT


def ranked(
    candidates: list[tuple], preferred: tuple[str, ...] = (), currency: str | None = None
) -> list[str]:
    """`candidates`: (symbol, exchange code, exchange name, match source); `currency`: the
    instrument's, whose main exchanges are its home."""
    home = {code for code, traded in CURRENCIES.items() if traded == currency}

    def key(candidate: tuple):
        symbol, code, name, source = candidate
        return candidate_rank(
            symbol=symbol,
            exchange_code=code,
            exchange_name=name,
            matched_by=source,
            preferred=preferred,
            main=MAIN,
            home=home,
        )

    return [candidate[0] for candidate in sorted(candidates, key=key)]


ETF = [
    ("0XMP.IL", "IOB", "International Orderbook - London", ISIN),
    ("XMPW.F", "FRA", "Frankfurt", ISIN),
    ("XMPW.L", "LSE", "London", ISIN),
    ("XMPW.DE", "GER", "XETRA", ISIN),
    ("XMPW.MI", "MIL", "Milan", HINT),
]


def test_main_exchanges_in_their_order_then_other_venues_by_code() -> None:
    assert ranked(ETF) == ["XMPW.MI", "XMPW.DE", "XMPW.L", "XMPW.F", "0XMP.IL"]


def test_the_provider_order_plays_no_part() -> None:
    assert ranked(ETF) == ranked(list(reversed(ETF)))


def test_preferred_exchanges_match_a_code_or_a_name_ignoring_case() -> None:
    assert ranked(ETF, ("london",))[0] == "XMPW.L"
    assert ranked(ETF, ("ger",))[0] == "XMPW.DE"
    assert ranked(ETF, (" Frankfurt ", "IOB"))[:2] == ["XMPW.F", "0XMP.IL"]
    assert ranked(ETF, ("NYSE",)) == ranked(ETF)  # a preference matching nothing changes nothing


def test_within_one_exchange_isin_before_hint_then_shorter_symbols() -> None:
    london = [
        ("19999EUR.L", "LSE", None, HINT),
        ("XMPWEUR.L", "LSE", None, HINT),
        ("XMPL.L", "LSE", None, HINT),
        ("XMPWXX.L", "LSE", "London", ISIN),
    ]
    assert ranked(london) == ["XMPWXX.L", "XMPL.L", "XMPWEUR.L", "19999EUR.L"]


def test_a_us_listing_without_suffix_is_on_a_main_exchange() -> None:
    us = [("0XMU.IL", "IOB", None, ISIN), ("XMPU", "NMS", "NASDAQ", ISIN)]
    assert ranked(us) == ["XMPU", "0XMU.IL"]


def test_a_candidate_without_exchange_comes_last() -> None:
    assert ranked([("XMPA", None, None, ISIN), ("XMPW.F", "FRA", None, ISIN)]) == [
        "XMPW.F",
        "XMPA",
    ]


# --- home exchanges: the instrument's currency (D-152) ---------------------------------------

# An Apple-like share: its US line, a USD line on London from a hint, a EUR line on Xetra.
US_SHARE = [
    ("0XMU.L", "LSE", None, HINT),
    ("XMPU.DE", "GER", None, HINT),
    ("XMPU", "NMS", "NASDAQ", ISIN),
    ("0XMU.IL", "IOB", None, ISIN),
]


def test_usd_puts_the_us_exchange_before_london() -> None:
    assert ranked(US_SHARE) == ["XMPU.DE", "0XMU.L", "XMPU", "0XMU.IL"]  # without a currency
    assert ranked(US_SHARE, currency="USD") == ["XMPU", "XMPU.DE", "0XMU.L", "0XMU.IL"]


def test_gbp_puts_london_first() -> None:
    assert ranked(ETF, currency="GBP") == ["XMPW.L", "XMPW.MI", "XMPW.DE", "XMPW.F", "0XMP.IL"]


def test_eur_keeps_the_declared_order_milan_first() -> None:
    assert ranked(ETF, currency="EUR") == ["XMPW.MI", "XMPW.DE", "XMPW.L", "XMPW.F", "0XMP.IL"]
    assert ranked(ETF[:4], currency="EUR")[:2] == ["XMPW.DE", "XMPW.L"]  # no Milan: Xetra


def test_chf_puts_six_first() -> None:
    six = [*ETF, ("XMPW.SW", "EBS", "Swiss", ISIN)]
    assert ranked(six, currency="CHF")[0] == "XMPW.SW"
    assert ranked(six)[:4] == ["XMPW.MI", "XMPW.DE", "XMPW.L", "XMPW.SW"]


def test_a_configured_exchange_still_comes_first_in_another_currency() -> None:
    assert ranked(US_SHARE, ("LSE",), currency="USD")[:2] == ["0XMU.L", "XMPU"]
    assert ranked(ETF, ("london",), currency="EUR")[:2] == ["XMPW.L", "XMPW.MI"]


def test_a_currency_without_a_home_exchange_ranks_as_without_currency() -> None:
    assert ranked(ETF, currency="SEK") == ranked(ETF)
    assert ranked(US_SHARE, currency="SEK") == ranked(US_SHARE)
