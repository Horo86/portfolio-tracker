"""The fixed rank of an engine's candidates for one instrument (D-125, D-136).

Candidates are ranked before any of them is checked, so the provider's own result order
plays no part and the cap on checks (Yahoo: `MAX_CANDIDATES`) never leaves out a listing on
a main exchange because other venues were listed first. The first accepted candidate in
this rank becomes the instrument's listing for that engine.

Rank, best first:
1. the preferred exchanges (`PT_PRICE_EXCHANGES`), in their order; each entry is compared,
   ignoring case, with both the exchange code and the exchange name;
2. the engine's main exchanges that trade in the instrument's currency (`home`, from
   `Capabilities.home_exchanges`, D-152), in the declared order;
3. the engine's other main exchanges (`Capabilities.main_exchanges`), in their order;
4. any other venue (an international order book, a regional exchange, an over-the-counter
   line), by exchange code in alphabetical order; a candidate without exchange last.

Within one exchange: found by ISIN before found by hint, then shorter symbols (alternative
trading lines carry longer codes), then alphabetical order.
"""

from collections.abc import Collection, Sequence

from portfolio_tracker.marketdata.base import MatchSource

PREFERRED, HOME, MAIN, OTHER = 0, 1, 2, 3

type Rank = tuple[int, int, str, int, int, str]


def _fold(text: str | None) -> str:
    return text.strip().casefold() if text else ""


def candidate_rank(
    *,
    symbol: str,
    exchange_code: str | None,
    exchange_name: str | None,
    matched_by: MatchSource,
    preferred: Sequence[str],
    main: Sequence[str],
    home: Collection[str] = (),
) -> Rank:
    """The sort key of one candidate: lower ranks first."""
    code, name = _fold(exchange_code), _fold(exchange_name)
    names = {code, name} - {""}
    group, position, venue = OTHER, 0 if names else 1, code or name  # unknown venue last
    for index, entry in enumerate(preferred):
        if _fold(entry) in names:
            group, position, venue = PREFERRED, index, ""
            break
    else:
        mains = [_fold(entry) for entry in main]
        if code and code in mains:
            homes = {_fold(entry) for entry in home}
            group, position, venue = HOME if code in homes else MAIN, mains.index(code), ""
    source = 0 if matched_by is MatchSource.ISIN else 1
    return (group, position, venue, source, len(symbol), symbol)
