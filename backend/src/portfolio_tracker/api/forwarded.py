"""The client's address and scheme behind trusted reverse proxies (D-164).

`resolve_client` is the one place that decides them. Only when the direct peer is in
`PT_TRUSTED_PROXIES` are `X-Forwarded-For` and `X-Forwarded-Proto` read; otherwise the client
is the direct peer and the connection's scheme, whatever the headers say (D-82 (6)).
`ForwardedHeaders` writes the result into the request's scope, so everything that reads
`request.client` or `request.url.scheme` (the login throttle, the audit IP, the logs, the
"plain http" warning) and uvicorn's access log agree. Forwarded headers from a peer that is
not trusted are ignored and logged once per start, without the address (D-175 (5)).
"""

import logging
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

Network = IPv4Network | IPv6Network
Address = IPv4Address | IPv6Address

_SCHEMES = frozenset({"http", "https"})
_FORWARDED = (b"x-forwarded-for", b"x-forwarded-proto")

log = logging.getLogger(__name__)


def parse_trusted_proxies(text: str) -> tuple[Network, ...]:
    """`PT_TRUSTED_PROXIES`: comma-separated addresses or networks (CIDR), empty for none.
    Any invalid entry raises `ValueError` with a message naming it: never ignored."""
    entries = [entry.strip() for entry in text.split(",")]
    if not any(entries):
        return ()
    networks = []
    for entry in entries:
        if not entry:
            raise ValueError("PT_TRUSTED_PROXIES has an empty entry (two commas, or a last comma)")
        try:
            network = ip_network(entry, strict=False)
        except ValueError:
            raise ValueError(
                f"PT_TRUSTED_PROXIES: {entry!r} is not an IP address or a network (CIDR)"
            ) from None
        if network.prefixlen == 0:
            raise ValueError(
                f"PT_TRUSTED_PROXIES: {entry!r} would trust every address; "
                "list only your reverse proxy's address"
            )
        networks.append(network)
    return tuple(networks)


@dataclass(frozen=True, slots=True)
class Client:
    ip: str | None
    scheme: str


def _address(text: str) -> Address | None:
    """A bare IPv4 or IPv6 address (an IPv4-mapped one as IPv4), or None for anything else:
    ports, brackets, names, empty."""
    try:
        address = ip_address(text.strip())
    except ValueError:
        return None
    if isinstance(address, IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _trusted(address: Address, trusted: tuple[Network, ...]) -> bool:
    return any(address in network for network in trusted)


def _forwarded_for(headers: Headers, trusted: tuple[Network, ...]) -> str | None:
    """The rightmost hop that is not a trusted proxy; None (use the peer) when the header is
    missing or a hop read on the way is malformed. Hops to its left are never read: a client
    can write anything there."""
    values = headers.getlist("x-forwarded-for")
    if not values:
        return None
    leftmost = None
    for hop in reversed(",".join(values).split(",")):
        address = _address(hop)
        if address is None:
            return None
        if not _trusted(address, trusted):
            return str(address)
        leftmost = address
    return str(leftmost)  # only trusted proxies: the first of them


def _forwarded_proto(headers: Headers) -> str | None:
    """One value, `http` or `https`; anything else (a list, an unknown scheme) is ignored."""
    values = headers.getlist("x-forwarded-proto")
    if len(values) != 1:
        return None
    value = values[0].strip().lower()
    return value if value in _SCHEMES else None


def _from_trusted(peer: str | None, trusted: tuple[Network, ...]) -> bool:
    address = _address(peer) if peer and trusted else None
    return address is not None and _trusted(address, trusted)


def resolve_client(
    peer: str | None, scheme: str, headers: Headers, trusted: tuple[Network, ...]
) -> Client:
    """The client's address and scheme for a request from `peer` over `scheme`."""
    if not _from_trusted(peer, trusted):
        return Client(peer, scheme)
    return Client(_forwarded_for(headers, trusted) or peer, _forwarded_proto(headers) or scheme)


class ForwardedHeaders:
    """Pure ASGI: rewrites `client` and `scheme` in place (uvicorn's access log reads the
    same scope after the app has run). Installed also when nobody is trusted, for the warning:
    one per app, so once per start."""

    def __init__(self, app: ASGIApp, *, trusted: tuple[Network, ...]) -> None:
        self.app = app
        self.trusted = trusted
        self.warned = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            peer = scope.get("client")
            if not self.warned and not _from_trusted(peer[0] if peer else None, self.trusted):
                self._warn_if_forwarded(scope)
            client = resolve_client(
                peer[0] if peer else None, scope["scheme"], Headers(scope=scope), self.trusted
            )
            if peer and client.ip != peer[0]:
                scope["client"] = (client.ip, 0)
            scope["scheme"] = client.scheme
        await self.app(scope, receive, send)

    def _warn_if_forwarded(self, scope: Scope) -> None:
        """A proxy left out of `PT_TRUSTED_PROXIES` makes every visitor share its address (the
        login limits, the audit). Said once, without the address or any header value."""
        if any(name in _FORWARDED for name, _ in scope.get("headers", ())):
            self.warned = True
            log.warning(
                "X-Forwarded-For or X-Forwarded-Proto came from an address that is not in "
                "PT_TRUSTED_PROXIES: the headers are ignored. Behind a reverse proxy, set "
                "PT_TRUSTED_PROXIES to its address (README, Behind a reverse proxy)"
            )
