"""Provider plug-ins by name, and the configured order (D-117, D-123).

Each adapter registers a factory under a short name. `PT_PRICE_PROVIDERS` lists the price
providers to use, in order; the first one with a compatible listing feeds an instrument
(D-125). A keyed provider is built only when its credential exists; otherwise it is skipped
(and reported), never failing the others.

Adding a provider means writing one adapter and registering it here: the engine, the API and
the data model do not change. Tests build their own `Registry` with fake providers.
"""

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from portfolio_tracker.marketdata.base import FxProvider, PriceProvider
from portfolio_tracker.marketdata.http import Transport

log = logging.getLogger(__name__)

_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")


class ProviderConfigError(ValueError):
    """The configured provider list cannot be used (unknown or repeated name, bad syntax)."""


@dataclass(frozen=True, slots=True)
class ProviderContext:
    """What a factory gets to build its adapter. `credential` is the decrypted credential of
    that provider (keyed providers only); adapters keep it out of logs and errors."""

    transport: Transport
    credential: str | None = None


@dataclass(frozen=True, slots=True)
class PriceProviderSpec:
    name: str
    factory: Callable[[ProviderContext], PriceProvider]
    needs_credential: bool = False


@dataclass(frozen=True, slots=True)
class FxProviderSpec:
    name: str
    factory: Callable[[ProviderContext], FxProvider]


@dataclass(frozen=True, slots=True)
class BuiltProviders:
    providers: tuple[PriceProvider, ...]
    skipped: tuple[str, ...] = ()  # keyed providers without a credential


def parse_provider_order(text: str) -> tuple[str, ...]:
    """`"yahoo, other"` → `("yahoo", "other")`. Names are lower-cased; an empty value means
    no price provider (manual prices only). Repeated or malformed names are refused."""
    names = tuple(part.strip().lower() for part in text.split(",")) if text.strip() else ()
    for name in names:
        if not _NAME.fullmatch(name):
            raise ProviderConfigError(f"invalid provider name {name!r}")
    if len(set(names)) != len(names):
        raise ProviderConfigError("a provider is listed twice")
    return names


@dataclass
class Registry:
    _price: dict[str, PriceProviderSpec] = field(default_factory=dict)
    _fx: dict[str, FxProviderSpec] = field(default_factory=dict)

    def register_price(self, spec: PriceProviderSpec) -> None:
        self._register(self._price, spec.name, spec)

    def register_fx(self, spec: FxProviderSpec) -> None:
        self._register(self._fx, spec.name, spec)

    @staticmethod
    def _register(table: dict, name: str, spec: object) -> None:
        if not _NAME.fullmatch(name):
            raise ValueError(f"invalid provider name {name!r}")
        if name in table:
            raise ValueError(f"provider {name!r} is already registered")
        table[name] = spec

    @property
    def price_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._price))

    @property
    def fx_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._fx))

    def price_specs(self, order: Sequence[str]) -> tuple[PriceProviderSpec, ...]:
        """The specs of the configured order; an unknown name is a configuration error,
        reported at startup with the names that exist."""
        unknown = [name for name in order if name not in self._price]
        if unknown:
            raise ProviderConfigError(
                f"unknown price provider(s) {', '.join(unknown)}; "
                f"available: {', '.join(self.price_names) or 'none'}"
            )
        return tuple(self._price[name] for name in order)

    def build_price_providers(
        self,
        order: Sequence[str],
        transport: Transport,
        credential_for: Callable[[str], str | None] = lambda _name: None,
    ) -> BuiltProviders:
        providers: list[PriceProvider] = []
        skipped: list[str] = []
        for spec in self.price_specs(order):
            credential = credential_for(spec.name) if spec.needs_credential else None
            if spec.needs_credential and not credential:
                log.warning("price provider skipped: no credential", extra={"provider": spec.name})
                skipped.append(spec.name)
                continue
            providers.append(spec.factory(ProviderContext(transport, credential)))
        return BuiltProviders(tuple(providers), tuple(skipped))

    def build_fx_provider(self, name: str, transport: Transport) -> FxProvider:
        spec = self._fx.get(name)
        if spec is None:
            raise ProviderConfigError(
                f"unknown FX provider {name!r}; available: {', '.join(self.fx_names) or 'none'}"
            )
        return spec.factory(ProviderContext(transport))


def default_registry() -> Registry:
    """The providers shipped with the app. Adapters are added here as they are written
    (Frankfurter and Yahoo in M4)."""
    return Registry()
