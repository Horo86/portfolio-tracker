"""The engine configuration: which engines are on, their order per role, and the preferred
exchanges (D-125, D-132).

It is stored in the database (`repositories/market_config.py`); the environment gives the
initial values only (`Registry.initial_config`). This module holds the value and the parsing
of the environment's comma-separated lists; it knows no concrete engine.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from portfolio_tracker.marketdata.base import Role

NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_EXCHANGE = re.compile(r"[^\s,][^,]{0,63}")


class ProviderConfigError(ValueError):
    """A configuration cannot be used (unknown or repeated name, an engine in an order of a
    role it does not serve, bad syntax)."""


@dataclass(frozen=True)
class RoutingConfig:
    """`orders` has every role; a name may be in an order while disabled (it keeps its place
    and is skipped, D-132)."""

    enabled: frozenset[str]
    orders: Mapping[Role, tuple[str, ...]] = field(default_factory=dict)
    exchanges: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        orders = {role: tuple(self.orders.get(role, ())) for role in Role}
        object.__setattr__(self, "orders", orders)
        for role, names in orders.items():
            if len(set(names)) != len(names):
                raise ProviderConfigError(f"an engine is listed twice for {role}")

    def order(self, role: Role) -> tuple[str, ...]:
        """The configured order of `role`, disabled engines included."""
        return self.orders[role]

    def active(self, role: Role) -> tuple[str, ...]:
        """The enabled engines of `role`, in order."""
        return tuple(name for name in self.orders[role] if name in self.enabled)

    def names(self) -> tuple[str, ...]:
        """Every name in some order or enabled, sorted."""
        listed: set[str] = set(self.enabled)
        for names in self.orders.values():
            listed.update(names)
        return tuple(sorted(listed))


def parse_provider_order(text: str) -> tuple[str, ...]:
    """`"yahoo, other"` → `("yahoo", "other")`. Names are lower-cased; an empty value means
    no engine for that role. Repeated or malformed names are refused."""
    names = tuple(part.strip().lower() for part in text.split(",")) if text.strip() else ()
    for name in names:
        if not NAME.fullmatch(name):
            raise ProviderConfigError(f"invalid provider name {name!r}")
    _no_repeats(names, "a provider is listed twice")
    return names


def parse_exchanges(text: str) -> tuple[str, ...]:
    """`"MIL, xetra"` → `("MIL", "xetra")`: provider exchange codes or names, compared
    ignoring case (D-125). An empty value means no preference."""
    exchanges = tuple(part.strip() for part in text.split(",")) if text.strip() else ()
    for exchange in exchanges:
        if not _EXCHANGE.fullmatch(exchange):
            raise ProviderConfigError(f"invalid exchange {exchange!r}")
    _no_repeats((e.casefold() for e in exchanges), "an exchange is listed twice")
    return exchanges


def _no_repeats(values: Iterable[str], message: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ProviderConfigError(message)
        seen.add(value)
