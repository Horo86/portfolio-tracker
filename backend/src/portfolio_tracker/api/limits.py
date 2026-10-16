"""Request body bounds, applied before the body is read (D-101, D-114).

FastAPI reads a JSON body whole before any handler or authentication runs, so without a
bound anyone could send a huge body to any route (also `/auth/login`). This ASGI middleware
bounds every request without buffering it: a declared `Content-Length` over the limit is
refused before a byte is read, and a body sent without one (chunked) is counted as it
streams and refused as soon as it passes the limit.

- The two import uploads: the base64 of a 10 MB file plus room for the choices, refused with
  413 `IMPORT_TOO_LARGE` (the code of a file that is too large once decoded).
- Every other route: 4 MiB, refused with 413 `REQUEST_TOO_LARGE`. The largest legitimate
  body is a bulk edit of 1,000 transactions (about 3 MB with notes); a future upload route
  (the backup restore of M6/M7) needs its own, higher bound here.
"""

from collections.abc import Callable
from dataclasses import dataclass

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from portfolio_tracker.api.errors import error_response
from portfolio_tracker.importers.base import MAX_BASE64_CHARS

MAX_REQUEST_BODY = 4 * 1024 * 1024
# The base64 text plus room for the other fields (file name, choices, up to 20,000 selected
# line keys of about 15 characters each).
MAX_IMPORT_BODY = MAX_BASE64_CHARS + 1024 * 1024
# Matched by suffix, so the bound still applies when the app is served under a sub-path.
IMPORT_UPLOADS = ("/api/v1/imports/preview", "/api/v1/imports/confirm")


@dataclass(frozen=True, slots=True)
class Bound:
    limit: int
    code: str  # the 413 error code

    def response(self):
        return error_response(413, self.code, f"the request is larger than {self.limit} bytes")


IMPORT_BOUND = Bound(MAX_IMPORT_BODY, "IMPORT_TOO_LARGE")
DEFAULT_BOUND = Bound(MAX_REQUEST_BODY, "REQUEST_TOO_LARGE")


def bound_for(path: str) -> Bound:
    return IMPORT_BOUND if path.endswith(IMPORT_UPLOADS) else DEFAULT_BOUND


class BodyTooLarge(HTTPException):
    """Raised from `receive` while the app reads the body: FastAPI re-raises HTTPExceptions
    from body parsing (any other exception would become a 400)."""

    def __init__(self, bound: Bound) -> None:
        super().__init__(status_code=413)
        self.bound = bound


async def body_too_large(_request, exc: BodyTooLarge):
    return exc.bound.response()


class BodyLimit:
    def __init__(self, app: ASGIApp, bound_for: Callable[[str], Bound]) -> None:
        self.app = app
        self.bound_for = bound_for

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        bound = self.bound_for(scope["path"])
        declared = _content_length(scope)
        if declared is not None and declared > bound.limit:
            await bound.response()(scope, receive, send)  # the body is never read
            return
        received = 0

        async def bounded() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > bound.limit:
                    raise BodyTooLarge(bound)
            return message

        await self.app(scope, bounded, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
