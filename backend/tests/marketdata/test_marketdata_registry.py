"""Provider registry and configured order (D-117, D-123), with providers defined here only."""

import logging
from collections.abc import Sequence
from datetime import date

import pytest
from fakes import FakeTransport, queue

from portfolio_tracker.config import Settings
from portfolio_tracker.marketdata.base import Close, Listing, Rate
from portfolio_tracker.marketdata.registry import (
    FxProviderSpec,
    PriceProviderSpec,
    ProviderConfigError,
    ProviderContext,
    Registry,
    default_registry,
    parse_provider_order,
)


class _Price:
    def __init__(self, name: str, context: ProviderContext) -> None:
        self.name = name
        self.context = context

    def search(self, query: str) -> Sequence[Listing]:
        return ()

    def daily_closes(self, symbol: str, start: date, end: date) -> Sequence[Close]:
        return ()


class _Fx:
    name = "fx-test"

    def __init__(self, context: ProviderContext) -> None:
        self.context = context

    def rates(self, currencies: Sequence[str], start: date, end: date) -> Sequence[Rate]:
        return ()


def _registry() -> Registry:
    registry = Registry()
    registry.register_price(PriceProviderSpec("alpha", lambda ctx: _Price("alpha", ctx)))
    registry.register_price(
        PriceProviderSpec("keyed", lambda ctx: _Price("keyed", ctx), needs_credential=True)
    )
    registry.register_price(PriceProviderSpec("beta", lambda ctx: _Price("beta", ctx)))
    registry.register_fx(FxProviderSpec("fx-test", _Fx))
    return registry


TRANSPORT = FakeTransport(queue())


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


def test_the_setting_defaults_to_yahoo(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings().price_providers == "yahoo"
    monkeypatch.setenv("PT_PRICE_PROVIDERS", "beta,alpha")
    assert parse_provider_order(Settings().price_providers) == ("beta", "alpha")


def test_build_follows_the_configured_order() -> None:
    built = _registry().build_price_providers(("beta", "alpha"), TRANSPORT)
    assert [p.name for p in built.providers] == ["beta", "alpha"]
    assert built.skipped == ()
    assert all(p.context.transport is TRANSPORT for p in built.providers)  # type: ignore[attr-defined]
    assert all(p.context.credential is None for p in built.providers)  # type: ignore[attr-defined]


def test_unknown_names_are_reported_with_the_available_ones() -> None:
    with pytest.raises(ProviderConfigError, match=r"nope.*available: alpha, beta, keyed"):
        _registry().build_price_providers(("alpha", "nope"), TRANSPORT)
    with pytest.raises(ProviderConfigError, match="available: none"):
        Registry().price_specs(("yahoo",))


def test_a_keyed_provider_gets_only_its_own_credential() -> None:
    asked: list[str] = []

    def credential_for(name: str) -> str | None:
        asked.append(name)
        return f"cred-of-{name}"

    built = _registry().build_price_providers(("alpha", "keyed"), TRANSPORT, credential_for)
    assert asked == ["keyed"]  # keyless providers never see credentials
    keyed = built.providers[1]
    assert keyed.context.credential == "cred-of-keyed"  # type: ignore[attr-defined]


def test_a_keyed_provider_without_credential_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        built = _registry().build_price_providers(("keyed", "beta"), TRANSPORT, lambda _n: None)
    assert [p.name for p in built.providers] == ["beta"]
    assert built.skipped == ("keyed",)
    assert "no credential" in caplog.text


def test_registration_rules() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="already registered"):
        registry.register_price(PriceProviderSpec("alpha", lambda ctx: _Price("alpha", ctx)))
    with pytest.raises(ValueError, match="invalid provider name"):
        registry.register_price(PriceProviderSpec("Bad Name", lambda ctx: _Price("x", ctx)))
    assert registry.price_names == ("alpha", "beta", "keyed")
    assert registry.fx_names == ("fx-test",)


def test_fx_provider_by_name() -> None:
    fx = _registry().build_fx_provider("fx-test", TRANSPORT)
    assert fx.context.transport is TRANSPORT  # type: ignore[attr-defined]
    with pytest.raises(ProviderConfigError, match="unknown FX provider"):
        _registry().build_fx_provider("other", TRANSPORT)


def test_default_registry_has_no_test_providers() -> None:
    registry = default_registry()
    assert "alpha" not in registry.price_names
    assert "keyed" not in registry.price_names
