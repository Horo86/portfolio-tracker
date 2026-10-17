"""The engine configuration value and the parsing of the environment's lists (D-125, D-132)."""

import pytest

from portfolio_tracker.marketdata.base import Role
from portfolio_tracker.marketdata.routing import (
    ProviderConfigError,
    RoutingConfig,
    parse_exchanges,
    parse_provider_order,
)


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("yahoo", ("yahoo",)),
        (" Alpha , beta ", ("alpha", "beta")),
        ("", ()),
        ("   ", ()),
        ("keyed-2,x_y", ("keyed-2", "x_y")),
    ],
)
def test_parse_provider_order(text: str, names: tuple[str, ...]) -> None:
    assert parse_provider_order(text) == names


@pytest.mark.parametrize(
    "text", ["alpha,,beta", "alpha,alpha", "al pha", "9lives", "a;b", "x" * 33]
)
def test_parse_provider_order_refuses_bad_lists(text: str) -> None:
    with pytest.raises(ProviderConfigError):
        parse_provider_order(text)


@pytest.mark.parametrize(
    ("text", "exchanges"),
    [
        ("", ()),
        (" MIL , xetra ", ("MIL", "xetra")),
        ("Borsa Italiana", ("Borsa Italiana",)),
    ],
)
def test_parse_exchanges(text: str, exchanges: tuple[str, ...]) -> None:
    assert parse_exchanges(text) == exchanges


@pytest.mark.parametrize("text", ["MIL,,XETRA", "MIL, mil", "x" * 65])
def test_parse_exchanges_refuses_bad_lists(text: str) -> None:
    with pytest.raises(ProviderConfigError):
        parse_exchanges(text)


def test_a_disabled_engine_keeps_its_place() -> None:
    config = RoutingConfig(
        frozenset({"alpha", "gamma"}), {Role.ETF_STOCK: ("alpha", "beta", "gamma")}
    )
    assert config.order(Role.ETF_STOCK) == ("alpha", "beta", "gamma")
    assert config.active(Role.ETF_STOCK) == ("alpha", "gamma")
    assert config.order(Role.BOND) == ()  # every role is present
    assert config.names() == ("alpha", "beta", "gamma")


def test_an_order_cannot_repeat_an_engine() -> None:
    with pytest.raises(ProviderConfigError, match="listed twice for FX"):
        RoutingConfig(frozenset(), {Role.FX: ("a", "a")})
