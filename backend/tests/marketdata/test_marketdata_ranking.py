"""The rank of an engine's candidates for one instrument (D-125, D-136): preferred exchanges,
then the engine's main exchanges, then other venues; within an exchange, ISIN before hint,
shorter symbols first. Exchange codes and names here are Yahoo-like; symbols are invented."""

from portfolio_tracker.marketdata.base import MatchSource
from portfolio_tracker.marketdata.ranking import candidate_rank

MAIN = ("MIL", "GER", "LSE", "NMS")
ISIN, HINT = MatchSource.ISIN, MatchSource.HINT


def ranked(candidates: list[tuple], preferred: tuple[str, ...] = ()) -> list[str]:
    """`candidates`: (symbol, exchange code, exchange name, match source)."""

    def key(candidate: tuple):
        symbol, code, name, source = candidate
        return candidate_rank(
            symbol=symbol,
            exchange_code=code,
            exchange_name=name,
            matched_by=source,
            preferred=preferred,
            main=MAIN,
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
