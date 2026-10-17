"""Engine registry, declared capabilities and building a configuration (D-117, D-131, D-132),
with engines defined here only."""

import logging
from collections.abc import Sequence
from datetime import date

import pytest
from fakes import FakeTransport, queue

from portfolio_tracker.config import Settings
from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.base import (
    Capabilities,
    CloseSeries,
    Identifier,
    Listing,
    PriceClass,
    Rate,
    Role,
    SearchQuery,
    SecurityFacts,
)
from portfolio_tracker.marketdata.registry import (
    ProviderConfigError,
    ProviderContext,
    ProviderSpec,
    Registry,
    default_registry,
)
from portfolio_tracker.marketdata.routing import RoutingConfig

EQUITY_UNITS = frozenset({QuoteUnit.CURRENCY, QuoteUnit.MINOR_CURRENCY_UNIT})
PRICES = Capabilities(roles=frozenset({Role.ETF_STOCK}), search_isin=True, units=EQUITY_UNITS)
KEYED = Capabilities(
    roles=frozenset({Role.ETF_STOCK}),
    search_isin=True,
    units=EQUITY_UNITS,
    needs_credential=True,
    credential_label="api_key",
)
BONDS = Capabilities(
    roles=frozenset({Role.BOND, Role.CLASSIFY}),
    search_isin=True,
    units=frozenset({QuoteUnit.PERCENT_OF_NOMINAL}),
)
FX = Capabilities(roles=frozenset({Role.FX}))


class _Engine:
    """Serves every role: the registry decides which roles it is used for."""

    def __init__(self, name: str, capabilities: Capabilities, context: ProviderContext) -> None:
        self.name = name
        self.capabilities = capabilities
        self.context = context

    def search(self, query: SearchQuery, *, max_wait: float | None = None) -> Sequence[Listing]:
        return ()

    def daily_closes(self, symbol: str, start: date, end: date) -> CloseSeries:
        return CloseSeries("EUR", QuoteUnit.CURRENCY)

    def rates(self, currencies: Sequence[str], start: date, end: date) -> Sequence[Rate]:
        return ()

    def classify(self, isin: str) -> SecurityFacts | None:
        return None


def spec(name: str, capabilities: Capabilities) -> ProviderSpec:
    return ProviderSpec(name, lambda ctx: _Engine(name, capabilities, ctx), capabilities)


def _registry() -> Registry:
    registry = Registry()
    registry.register(spec("alpha", PRICES))
    registry.register(spec("keyed", KEYED))
    registry.register(spec("beta", PRICES))
    registry.register(spec("bonds", BONDS))
    registry.register(spec("fx-test", FX))
    return registry


TRANSPORT = FakeTransport(queue())


def config(enabled: Sequence[str], **orders: Sequence[str]) -> RoutingConfig:
    return RoutingConfig(
        frozenset(enabled), {Role(role.upper()): tuple(names) for role, names in orders.items()}
    )


def names(engines: Sequence[object]) -> list[str]:
    return [engine.name for engine in engines]  # type: ignore[attr-defined]


# --- capabilities --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"roles": frozenset()}, "at least one role"),
        ({"roles": frozenset({Role.ETF_STOCK}), "units": EQUITY_UNITS}, "search by ISIN"),
        (
            {
                "roles": frozenset({Role.BOND}),
                "search_isin": True,
                "units": EQUITY_UNITS,
            },
            "unit of that class",
        ),
        (
            {"roles": frozenset({Role.FX}), "units": frozenset({QuoteUnit.CURRENCY})},
            "price engines only",
        ),
        ({"roles": frozenset({Role.FX}), "credential_label": "key"}, "keyed engine"),
        ({"roles": frozenset({Role.FX}), "history_years": 0}, "at least 1"),
    ],
)
def test_capabilities_refuse_contradictions(fields: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Capabilities(**fields)


def test_a_search_query_has_an_isin_or_a_text() -> None:
    hint = Identifier("XMPW", "XMIL")
    query = SearchQuery.by_isin("ZZ0000000418", [hint])
    assert (query.value, query.hints) == ("ZZ0000000418", (hint,))
    assert SearchQuery.by_text("XMP").value == "XMP"
    with pytest.raises(ValueError, match="either an ISIN or a text"):
        SearchQuery()
    with pytest.raises(ValueError, match="either an ISIN or a text"):
        SearchQuery(isin="ZZ0000000418", text="XMP")
    with pytest.raises(ValueError, match="hints only go with an ISIN"):
        SearchQuery(text="XMP", hints=(hint,))


def test_the_shipped_engines_declare_their_roles() -> None:
    registry = default_registry()
    assert registry.names == ("frankfurter", "openfigi", "yahoo")
    yahoo = registry.spec("yahoo").capabilities
    assert yahoo.roles == {Role.ETF_STOCK, Role.CLASSIFY}
    assert yahoo.units == EQUITY_UNITS  # never % of nominal: no bond role (D-118)
    assert (yahoo.official, yahoo.needs_credential, yahoo.echoes_isin) == (False, False, False)
    assert registry.spec("frankfurter").capabilities.roles == {Role.FX}
    assert registry.spec("openfigi").capabilities.roles == {Role.CLASSIFY}
    assert registry.spec("openfigi").throttle.min_interval == 2.5  # 24 requests a minute
    assert registry.names_for(Role.BOND) == ()


def test_registration_rules() -> None:
    registry = _registry()
    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec("alpha", PRICES))
    with pytest.raises(ValueError, match="invalid provider name"):
        registry.register(spec("Bad Name", PRICES))
    assert registry.names == ("alpha", "beta", "bonds", "fx-test", "keyed")
    assert registry.names_for(Role.ETF_STOCK) == ("alpha", "beta", "keyed")
    assert registry.names_for(Role.CLASSIFY) == ("bonds",)
    with pytest.raises(ProviderConfigError, match="unknown provider 'nope'"):
        registry.spec("nope")


# --- orders and the initial configuration --------------------------------------------------


def test_an_order_only_holds_engines_serving_that_role() -> None:
    registry = _registry()
    assert registry.check_order(Role.BOND, ["bonds"]) == ("bonds",)
    with pytest.raises(ProviderConfigError, match=r"'alpha' does not serve BOND.*: bonds"):
        registry.check_order(Role.BOND, ["alpha"])
    with pytest.raises(ProviderConfigError, match=r"unknown provider 'nope' for FX.*: fx-test"):
        registry.check_order(Role.FX, ["nope"])
    with pytest.raises(ProviderConfigError, match="listed twice"):
        registry.check_order(Role.ETF_STOCK, ["alpha", "alpha"])


def test_the_initial_configuration_comes_from_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings()
    assert (settings.price_providers, settings.bond_price_providers) == ("yahoo", "")
    assert (settings.fx_providers, settings.price_exchanges) == ("frankfurter", "")
    initial = default_registry().initial_config(
        price_providers=settings.price_providers,
        bond_price_providers=settings.bond_price_providers,
        fx_providers=settings.fx_providers,
        exchanges=settings.price_exchanges,
    )
    assert initial.enabled == {"yahoo", "frankfurter"}
    assert initial.orders == {
        Role.ETF_STOCK: ("yahoo",),
        Role.BOND: (),
        Role.FX: ("frankfurter",),
        Role.CLASSIFY: ("yahoo",),  # the listed engines that declare it
    }
    assert initial.exchanges == ()

    monkeypatch.setenv("PT_BOND_PRICE_PROVIDERS", "bonds")
    monkeypatch.setenv("PT_PRICE_EXCHANGES", "MIL, xetra")
    settings = Settings()
    initial = _registry().initial_config(
        price_providers="Beta, alpha",
        bond_price_providers=settings.bond_price_providers,
        fx_providers="",
        exchanges=settings.price_exchanges,
    )
    assert initial.active(Role.ETF_STOCK) == ("beta", "alpha")
    assert initial.order(Role.CLASSIFY) == ("bonds",)
    assert initial.exchanges == ("MIL", "xetra")


def test_an_engine_in_the_wrong_environment_list_stops_the_start() -> None:
    with pytest.raises(ProviderConfigError, match="'yahoo' does not serve BOND"):
        default_registry().initial_config(price_providers="", bond_price_providers="yahoo")
    with pytest.raises(ProviderConfigError, match="unknown provider 'nope'"):
        default_registry().initial_config(price_providers="yahoo,nope")


# --- building -------------------------------------------------------------------------------


def test_build_follows_the_order_and_skips_disabled_engines() -> None:
    built = _registry().build(
        config(["beta", "alpha", "fx-test"], etf_stock=["beta", "keyed", "alpha"], fx=["fx-test"]),
        TRANSPORT,
    )
    assert names(built.prices(PriceClass.ETF_STOCK)) == ["beta", "alpha"]  # keyed is off
    assert names(built.prices(PriceClass.BOND)) == []
    assert names(built.fx()) == ["fx-test"]
    assert built.classifiers() == ()
    assert (built.skipped, built.ignored) == ((), ())
    engines = [*built.prices(PriceClass.ETF_STOCK), *built.fx()]
    assert all(e.context.transport is TRANSPORT for e in engines)  # type: ignore[attr-defined]
    assert all(e.context.credential is None for e in engines)  # type: ignore[attr-defined]


def test_an_engine_serving_two_roles_is_built_once() -> None:
    built = _registry().build(config(["bonds"], bond=["bonds"], classify=["bonds"]), TRANSPORT)
    assert built.prices(PriceClass.BOND)[0] is built.classifiers()[0]


def test_a_keyed_engine_gets_only_its_own_credential() -> None:
    asked: list[str] = []

    def credential_for(name: str) -> str | None:
        asked.append(name)
        return f"cred-of-{name}"

    built = _registry().build(
        config(["alpha", "keyed"], etf_stock=["alpha", "keyed"]), TRANSPORT, credential_for
    )
    assert asked == ["keyed"]  # keyless engines never see credentials
    keyed = built.prices(PriceClass.ETF_STOCK)[1]
    assert keyed.context.credential == "cred-of-keyed"  # type: ignore[attr-defined]


def test_a_keyed_engine_without_credential_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        built = _registry().build(
            config(["keyed", "beta"], etf_stock=["keyed", "beta"]), TRANSPORT, lambda _n: None
        )
    assert names(built.prices(PriceClass.ETF_STOCK)) == ["beta"]
    assert built.skipped == ("keyed",)
    assert "no credential" in caplog.text


def test_stored_names_this_version_cannot_use_are_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stored = config(["gone", "alpha"], etf_stock=["gone", "alpha"], bond=["alpha"])
    with caplog.at_level(logging.WARNING):
        built = _registry().build(stored, TRANSPORT)
    assert names(built.prices(PriceClass.ETF_STOCK)) == ["alpha"]
    assert names(built.prices(PriceClass.BOND)) == []  # alpha does not serve BOND (any more)
    assert built.ignored == ((Role.ETF_STOCK, "gone"), (Role.BOND, "alpha"))
    assert "configured provider ignored" in caplog.text


def test_a_base_url_reaches_only_its_engine() -> None:
    built = _registry().build(
        config(["alpha", "fx-test"], etf_stock=["alpha"], fx=["fx-test"]),
        TRANSPORT,
        urls={"fx-test": "http://self.host"},
    )
    assert built.fx()[0].context.url == "http://self.host"  # type: ignore[attr-defined]
    assert built.prices(PriceClass.ETF_STOCK)[0].context.url is None  # type: ignore[attr-defined]


def test_one_throttle_per_engine_survives_rebuilds() -> None:
    registry = _registry()
    first = registry.build(config(["alpha", "beta"], etf_stock=["alpha", "beta"]), TRANSPORT)
    again = registry.build(config(["alpha"], etf_stock=["alpha"]), TRANSPORT)
    alpha_1, beta = (e.context.throttle for e in first.prices(PriceClass.ETF_STOCK))  # type: ignore[attr-defined]
    alpha_2 = again.prices(PriceClass.ETF_STOCK)[0].context.throttle  # type: ignore[attr-defined]
    assert alpha_1 is alpha_2 is registry.throttle("alpha")
    assert beta is not alpha_1
    with pytest.raises(ProviderConfigError):
        registry.throttle("nope")


def test_default_registry_has_no_test_engines() -> None:
    assert not {"alpha", "keyed", "bonds"} & set(default_registry().names)
