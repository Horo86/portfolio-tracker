"""The price sources for the Administrator (D-135, D-186, D-188): each engine's state, and
the three changes the admin API makes to the stored configuration (D-132): an engine on or
off, the order of a role, the preferred exchanges.

A change is one write transaction: the stored configuration is read again, compared, saved,
audited with its before and after, and the listing re-check is recorded (D-134); the caller
queues the run once it is committed. A change that changes nothing writes, audits and
requests nothing (D-188). Last write wins: no version is compared (D-186). Nothing here
deletes a price, a listing or a close: make before break and the level check apply in the
re-check run as for any configuration change (D-134). Leaving a role without an enabled
engine is allowed; the screen confirms it (D-188).

Only engine names, roles, codes, times and counts leave this module: never an ISIN, a symbol,
an instrument, a listing or a credential value (D-135).
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from sqlalchemy.orm import Session

from portfolio_tracker.marketdata.base import Role
from portfolio_tracker.marketdata.registry import ProviderSpec, Registry
from portfolio_tracker.marketdata.routing import RoutingConfig, check_exchanges
from portfolio_tracker.marketdata.throttle import ThrottleHealth
from portfolio_tracker.repositories import audit, credentials, instruments, market_config

ENTITY = "market_data_config"


class EngineStatus(StrEnum):
    """D-135's four values, computed whether the engine is on or off (D-188): the screen
    shows the status only next to an enabled engine."""

    WORKING = "WORKING"
    PAUSED = "PAUSED"  # waiting out a rate limit or a server error
    EXHAUSTED = "EXHAUSTED"  # refused until the next run after consecutive failures
    MISSING_CREDENTIAL = "MISSING_CREDENTIAL"  # a keyed engine without its credential


@dataclass(frozen=True, slots=True)
class EngineState:
    spec: ProviderSpec
    enabled: bool
    credential_updated_at: datetime | None
    health: ThrottleHealth
    status: EngineStatus
    checks: dict[str, int]


def stored(db: Session) -> RoutingConfig:
    """The stored configuration; seeded at every start, so always there for the routes."""
    config = market_config.load(db)
    if config is None:
        raise RuntimeError("the market data configuration has not been seeded")
    return config


def status_of(
    spec: ProviderSpec, credential_set: bool, health: ThrottleHealth, now: datetime
) -> EngineStatus:
    if spec.capabilities.needs_credential and not credential_set:
        return EngineStatus.MISSING_CREDENTIAL
    if health.exhausted:
        return EngineStatus.EXHAUSTED
    if health.paused_until is not None and health.paused_until > now:
        return EngineStatus.PAUSED
    return EngineStatus.WORKING


def engine_states(db: Session, registry: Registry, names: tuple[str, ...]) -> list[EngineState]:
    """The state of each named (registered) engine. The credential is never decrypted: only
    when it was stored."""
    config = stored(db)
    counts = instruments.check_counts(db)
    now = registry.now()
    states = []
    for name in names:
        spec = registry.spec(name)
        updated = credentials.credential_updated_at(db, name)
        health = registry.health(name)
        checks = {status: n for (engine, status), n in sorted(counts.items()) if engine == name}
        states.append(
            EngineState(
                spec,
                enabled=name in config.enabled,
                credential_updated_at=updated,
                health=health,
                status=status_of(spec, updated is not None, health, now),
                checks=checks,
            )
        )
    return states


def _change(
    db: Session,
    before: RoutingConfig,
    after: RoutingConfig,
    action: str,
    audit_before: dict,
    audit_after: dict,
    actor_id,
    ip: str | None,
    at: datetime,
) -> bool:
    if after == before:
        return False
    market_config.save(db, after)
    market_config.request_resolve(db, at)
    audit.record(
        db, action, actor_id=actor_id, entity_type=ENTITY, before=audit_before,
        after=audit_after, ip=ip,
    )  # fmt: skip
    return True


def set_enabled(
    db: Session, registry: Registry, name: str, enabled: bool, *, actor_id, ip, at
) -> bool:
    """Turn an engine on or off; returns whether anything changed. A keyed engine without
    its credential may be turned on: it reads `MISSING_CREDENTIAL` and the run skips it."""
    registry.spec(name)  # an unknown name raises ProviderConfigError
    config = stored(db)
    was = name in config.enabled
    now_enabled = config.enabled | {name} if enabled else config.enabled - {name}
    return _change(
        db,
        config,
        replace(config, enabled=frozenset(now_enabled)),
        "market_data.engine_enabled" if enabled else "market_data.engine_disabled",
        {"engine": name, "enabled": was},
        {"engine": name, "enabled": enabled},
        actor_id,
        ip,
        at,
    )


def set_order(
    db: Session, registry: Registry, role: Role, names: list[str], *, actor_id, ip, at
) -> bool:
    """Replace the order of `role` (disabled engines keep their place, D-132); refused with
    `ProviderConfigError` for an unknown name, a repeat, or an engine that does not declare
    `role` (D-131)."""
    order = registry.check_order(role, names)
    config = stored(db)
    orders = {**config.orders, role: order}
    return _change(
        db,
        config,
        replace(config, orders=orders),
        "market_data.routes_changed",
        {"role": role.value, "engines": list(config.order(role))},
        {"role": role.value, "engines": list(order)},
        actor_id,
        ip,
        at,
    )


def set_exchanges(db: Session, exchanges: list[str], *, actor_id, ip, at) -> bool:
    """Replace the preferred exchanges (D-125); refused with `ProviderConfigError` for a bad
    or repeated item."""
    checked = check_exchanges(exchanges)
    config = stored(db)
    return _change(
        db,
        config,
        replace(config, exchanges=checked),
        "market_data.exchanges_changed",
        {"exchanges": list(config.exchanges)},
        {"exchanges": list(checked)},
        actor_id,
        ip,
        at,
    )
