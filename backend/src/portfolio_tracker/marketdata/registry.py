"""Engines by name, their declared capabilities, and building them for a configuration
(D-117, D-131, D-132).

Each adapter registers one `ProviderSpec`: a name, a factory, its `Capabilities` and its
throttle policy. One engine may serve several roles (e.g. prices and classification) with one
adapter, one throttle and one credential. The configuration (`RoutingConfig`, stored in the
database) says which engines are on and their order per role; a keyed engine is built only
when its credential exists, otherwise it is skipped (and reported), never failing the others.

Adding an engine means writing one adapter and registering it in `default_registry`: the
engine, the API and the data model do not change (`docs/adding-a-provider.md`). Tests build
their own `Registry` with fake engines.
"""

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from portfolio_tracker.marketdata import frankfurter, openfigi, yahoo
from portfolio_tracker.marketdata.base import (
    PRICE_ROLES,
    Capabilities,
    Classifier,
    FxProvider,
    PriceClass,
    PriceProvider,
    Role,
)
from portfolio_tracker.marketdata.http import Transport
from portfolio_tracker.marketdata.routing import (
    NAME,
    ProviderConfigError,
    RoutingConfig,
    parse_exchanges,
    parse_provider_order,
)
from portfolio_tracker.marketdata.throttle import Throttle, ThrottlePolicy

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderContext:
    """What a factory gets to build its adapter. `credential` is the decrypted credential of
    that engine (keyed engines only); adapters keep it out of logs and errors."""

    transport: Transport
    throttle: Throttle
    credential: str | None = None
    url: str | None = None  # base URL override (e.g. a self-hosted Frankfurter, PT_FX_URL)


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """One engine. The factory returns an adapter implementing the protocol of every role in
    `capabilities.roles` (`base.PriceProvider`, `FxProvider`, `Classifier`)."""

    name: str
    factory: Callable[[ProviderContext], object]
    capabilities: Capabilities
    throttle: ThrottlePolicy = field(default_factory=ThrottlePolicy)


@dataclass(frozen=True, slots=True)
class BuiltEngines:
    """The adapters of the enabled engines, per role in the configured order."""

    by_role: Mapping[Role, tuple[object, ...]]
    skipped: tuple[str, ...] = ()  # keyed engines without a credential
    ignored: tuple[tuple[Role, str], ...] = ()  # placed where this version cannot use them

    def prices(self, price_class: PriceClass) -> tuple[PriceProvider, ...]:
        return cast(tuple[PriceProvider, ...], self.by_role.get(PRICE_ROLES[price_class], ()))

    def fx(self) -> tuple[FxProvider, ...]:
        return cast(tuple[FxProvider, ...], self.by_role.get(Role.FX, ()))

    def classifiers(self) -> tuple[Classifier, ...]:
        return cast(tuple[Classifier, ...], self.by_role.get(Role.CLASSIFY, ()))


@dataclass
class Registry:
    """Specs by name, and **one throttle per engine name** for the life of the registry: the
    queue and asset searches share it, and rebuilding an adapter (e.g. after a configuration
    change) never resets a pause or an exhaustion."""

    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _specs: dict[str, ProviderSpec] = field(default_factory=dict)
    _throttles: dict[str, Throttle] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, spec: ProviderSpec) -> None:
        if not NAME.fullmatch(spec.name):
            raise ValueError(f"invalid provider name {spec.name!r}")
        if spec.name in self._specs:
            raise ValueError(f"provider {spec.name!r} is already registered")
        self._specs[spec.name] = spec

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def names_for(self, role: Role) -> tuple[str, ...]:
        return tuple(name for name in self.names if role in self._specs[name].capabilities.roles)

    def spec(self, name: str) -> ProviderSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise ProviderConfigError(
                f"unknown provider {name!r}; available: {', '.join(self.names) or 'none'}"
            )
        return spec

    def throttle(self, name: str) -> Throttle:
        spec = self.spec(name)
        with self._lock:
            if name not in self._throttles:
                self._throttles[name] = Throttle(spec.throttle, self.clock, self.sleep)
            return self._throttles[name]

    def reset_throttles(self) -> None:
        """Clear every engine's failure count at the start of a run: an engine exhausted by
        consecutive failures is refused "until the next run" (ADR-0007)."""
        with self._lock:
            throttles = list(self._throttles.values())
        for throttle in throttles:
            throttle.reset()

    # --- configuration ----------------------------------------------------------------

    def check_order(self, role: Role, names: Sequence[str]) -> tuple[str, ...]:
        """An order for `role`, refused when a name is unknown, repeated or does not serve
        that role (the administrator can only place an engine where it works, D-131)."""
        if len(set(names)) != len(names):
            raise ProviderConfigError(f"an engine is listed twice for {role}")
        available = ", ".join(self.names_for(role)) or "none"
        for name in names:
            if name not in self._specs:
                raise ProviderConfigError(
                    f"unknown provider {name!r} for {role}; available: {available}"
                )
            if role not in self._specs[name].capabilities.roles:
                raise ProviderConfigError(
                    f"provider {name!r} does not serve {role}; available: {available}"
                )
        return tuple(names)

    def initial_config(
        self,
        *,
        price_providers: str,
        bond_price_providers: str = "",
        fx_providers: str = "",
        classify_providers: str = "",
        exchanges: str = "",
    ) -> RoutingConfig:
        """The configuration seeded from the environment (D-132): every listed engine is on,
        in the order of each list."""
        lists = {
            Role.ETF_STOCK: price_providers,
            Role.BOND: bond_price_providers,
            Role.FX: fx_providers,
            Role.CLASSIFY: classify_providers,
        }
        orders = {
            role: self.check_order(role, parse_provider_order(text)) for role, text in lists.items()
        }
        listed = frozenset(name for names in orders.values() for name in names)
        return RoutingConfig(listed, orders, parse_exchanges(exchanges))

    # --- building ---------------------------------------------------------------------

    def build(
        self,
        config: RoutingConfig,
        transport: Transport,
        credential_for: Callable[[str], str | None] = lambda _name: None,
        urls: Mapping[str, str] | None = None,
    ) -> BuiltEngines:
        """The enabled engines of `config`, each built once. A stored name that this version
        has no adapter for, or that no longer serves a role, is ignored with a warning; a
        keyed engine without its credential is skipped."""
        adapters: dict[str, object | None] = {}
        skipped: list[str] = []
        ignored: list[tuple[Role, str]] = []
        by_role: dict[Role, tuple[object, ...]] = {}
        for role in Role:
            built: list[object] = []
            for name in config.active(role):
                spec = self._specs.get(name)
                if spec is None or role not in spec.capabilities.roles:
                    log.warning(
                        "configured provider ignored", extra={"provider": name, "role": role}
                    )
                    ignored.append((role, name))
                    continue
                if name not in adapters:
                    adapters[name] = self._build_one(spec, transport, credential_for, urls or {})
                    if adapters[name] is None:
                        skipped.append(name)
                adapter = adapters[name]
                if adapter is not None:
                    built.append(adapter)
            by_role[role] = tuple(built)
        return BuiltEngines(by_role, tuple(skipped), tuple(ignored))

    def _build_one(
        self,
        spec: ProviderSpec,
        transport: Transport,
        credential_for: Callable[[str], str | None],
        urls: Mapping[str, str],
    ) -> object | None:
        needs_credential = spec.capabilities.needs_credential
        credential = credential_for(spec.name) if needs_credential else None
        if needs_credential and not credential:
            log.warning("provider skipped: no credential", extra={"provider": spec.name})
            return None
        context = ProviderContext(
            transport, self.throttle(spec.name), credential, urls.get(spec.name)
        )
        return spec.factory(context)


def _frankfurter(context: ProviderContext) -> frankfurter.FrankfurterFx:
    url = context.url or frankfurter.DEFAULT_URL
    return frankfurter.FrankfurterFx(context.transport, url, context.throttle)


def _openfigi(context: ProviderContext) -> openfigi.OpenFigiClassifier:
    return openfigi.OpenFigiClassifier(context.transport, context.throttle)


def _yahoo(context: ProviderContext) -> yahoo.YahooPrices:
    return yahoo.YahooPrices(context.transport, context.throttle)


def default_registry(
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep
) -> Registry:
    """The engines shipped with the app, each with its capabilities and throttle policy."""
    registry = Registry(clock, sleep)
    registry.register(
        ProviderSpec(
            frankfurter.NAME,
            _frankfurter,
            frankfurter.CAPABILITIES,
            ThrottlePolicy(min_interval=1.0),
        )
    )
    registry.register(
        ProviderSpec(openfigi.NAME, _openfigi, openfigi.CAPABILITIES, openfigi.THROTTLE)
    )
    registry.register(
        ProviderSpec(yahoo.NAME, _yahoo, yahoo.CAPABILITIES, ThrottlePolicy(min_interval=1.0))
    )
    return registry
