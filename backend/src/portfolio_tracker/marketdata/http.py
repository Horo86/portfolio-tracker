"""HTTP for market data adapters, on the standard library only (D-117).

Adapters never open connections themselves: they call a `Transport`, injected at build time.
The app uses `UrllibTransport`; tests pass a fake that serves synthetic fixtures, so no test
ever touches the network (and a guard in the tests makes any attempt fail loudly).

`UrllibTransport` enforces, for every call:
- only `http`/`https` URLs;
- a timeout for connecting and for every read, and a deadline for the whole response;
- a cap on the response body (`max_bytes`), checked before and while reading;
- redirects only to the same scheme, host and port (at most 3), never to another host.

Errors never quote URLs, query strings or bodies (a keyed provider may put its key in a
query string, and a body may echo the request).
"""

import http.client
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import IO, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from portfolio_tracker.marketdata.base import ErrorCode, ProviderError

DEFAULT_TIMEOUT = 15.0  # seconds, per connection attempt and per read
DEFAULT_DEADLINE = 60.0  # seconds for a whole response
_CHUNK = 64 * 1024
DEFAULT_MAX_BYTES = 8 * 1024 * 1024  # 20 years of daily closes is well under 1 MB
USER_AGENT = "portfolio-tracker"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)  # names in lower case
    body: bytes = b""

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class Transport(Protocol):
    def fetch(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        headers: Mapping[str, str] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> HttpResponse:
        """Send a GET request. Any HTTP status is returned as a response; only network
        failures, an oversized body and a refused redirect raise `ProviderError`."""
        ...


def with_query(url: str, params: Mapping[str, str] | None) -> str:
    if not params:
        return url
    return f"{url}{'&' if urlsplit(url).query else '?'}{urlencode(params)}"


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    default_port = {"http": 80, "https": 443}.get(parts.scheme)
    return parts.scheme, (parts.hostname or "").lower(), parts.port or default_port


class _SameOriginRedirects(HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> Request | None:
        if _origin(newurl) != _origin(req.full_url):
            fp.close()
            raise ProviderError(
                ErrorCode.REDIRECT_REFUSED, "redirect to another host refused", status=code
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Readable(Protocol):
    def read1(self, size: int, /) -> bytes: ...


def _is_count(text: str) -> bool:
    """ASCII digits only: `"²".isdigit()` is True but `int("²")` fails."""
    return text.isascii() and text.isdecimal()


def _read_capped(stream: _Readable, length: str | None, max_bytes: int, deadline: float) -> bytes:
    """Read at most `max_bytes`, in chunks, and give up at `deadline` (monotonic seconds): a
    server that drips one byte per timeout cannot hold the queue for hours."""
    too_large = ProviderError(ErrorCode.RESPONSE_TOO_LARGE, f"response over {max_bytes} bytes")
    if length is not None and _is_count(length.strip()) and int(length) > max_bytes:
        raise too_large
    chunks: list[bytes] = []
    size = 0
    while True:
        if time.monotonic() > deadline:
            raise ProviderError(ErrorCode.NETWORK, "response deadline exceeded")
        chunk = stream.read1(min(_CHUNK, max_bytes + 1 - size))  # what has arrived so far
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > max_bytes:
            raise too_large


class UrllibTransport:
    def __init__(self, user_agent: str = USER_AGENT, deadline: float = DEFAULT_DEADLINE) -> None:
        self._user_agent = user_agent
        self._deadline = deadline
        self._opener = build_opener(_SameOriginRedirects)

    def fetch(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        headers: Mapping[str, str] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> HttpResponse:
        if urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("market data URLs must use http or https")
        request = Request(
            with_query(url, params),
            headers={
                "User-Agent": self._user_agent,
                "Accept": "application/json",
                **(headers or {}),
            },
        )
        deadline = time.monotonic() + self._deadline
        try:
            try:
                with self._opener.open(request, timeout=timeout) as response:
                    return HttpResponse(
                        response.status,
                        {k.lower(): v for k, v in response.headers.items()},
                        _read_capped(
                            response, response.headers.get("Content-Length"), max_bytes, deadline
                        ),
                    )
            except HTTPError as error:
                with error:
                    if 300 <= error.code < 400:  # urllib refused it (scheme, loop)
                        raise ProviderError(
                            ErrorCode.REDIRECT_REFUSED, "redirect refused", status=error.code
                        ) from None
                    return HttpResponse(
                        error.code,
                        {k.lower(): v for k, v in (error.headers or {}).items()},
                        _read_capped(
                            error, error.headers.get("Content-Length"), max_bytes, deadline
                        )
                        if error.headers
                        else b"",
                    )
        except ProviderError:
            raise
        except (URLError, OSError, http.client.HTTPException) as error:
            # Type only: messages of these errors can quote the URL.
            raise ProviderError(ErrorCode.NETWORK, type(error).__name__) from None


# --- JSON over HTTP -----------------------------------------------------------------------


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """Seconds to wait from a Retry-After header (delay in seconds or an HTTP date)."""
    if not value:
        return None
    value = value.strip()
    if _is_count(value):
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


def check_status(response: HttpResponse, now: datetime) -> None:
    """Raise the `ProviderError` matching a non-2xx answer."""
    status = response.status
    if 200 <= status < 300:
        return
    retry_after = parse_retry_after(response.header("Retry-After"), now)
    if status == 429:
        raise ProviderError(
            ErrorCode.RATE_LIMITED, "rate limited", status=status, retry_after=retry_after
        )
    if status == 404:
        raise ProviderError(ErrorCode.NOT_FOUND, "not found", status=status)
    raise ProviderError(
        ErrorCode.HTTP_ERROR, f"HTTP {status}", status=status, retry_after=retry_after
    )


def _reject_constant(name: str) -> object:
    raise ValueError(f"{name} is not a number")


MAX_DIGITS = 40  # no price or rate needs more; bounds the work of formatting a number
MAX_EXPONENT = 30


def _bounded_decimal(text: str) -> Decimal:
    """A JSON number as an exact Decimal within sane bounds: `1e400` or `1e-400` would
    become Infinity or 0 after a float step, and `1e999999999` would format as a 1 GB
    string when stored."""
    value = Decimal(text)
    digits = value.as_tuple().digits
    if not value.is_finite() or len(digits) > MAX_DIGITS or abs(value.adjusted()) > MAX_EXPONENT:
        raise ValueError("number out of bounds")
    return value


def _bounded_int(text: str) -> int:
    if len(text.lstrip("-")) > MAX_DIGITS:
        raise ValueError("number out of bounds")
    return int(text)


def parse_json(body: bytes) -> object:
    """JSON with exact decimals: every non-integer number becomes a `Decimal`; NaN, Infinity
    and numbers beyond `MAX_DIGITS`/`MAX_EXPONENT` are refused. Anything unreadable is
    FORMAT_CHANGED."""
    try:
        return json.loads(
            body.decode("utf-8"),
            parse_float=_bounded_decimal,
            parse_int=_bounded_int,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ProviderError(ErrorCode.FORMAT_CHANGED, "response is not valid JSON") from None


def get_json(
    transport: Transport,
    url: str,
    params: Mapping[str, str] | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    headers: Mapping[str, str] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> object:
    response = transport.fetch(url, params, timeout=timeout, headers=headers, max_bytes=max_bytes)
    check_status(response, now())
    return parse_json(response.body)
