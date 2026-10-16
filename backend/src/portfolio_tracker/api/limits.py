"""Request body bound for the import uploads (D-101).

The 10 MB limit applies to the decoded file, but the body arrives base64-encoded in JSON
(about 4/3 larger), and FastAPI reads a JSON body whole before any handler runs. This ASGI
middleware bounds it first, without buffering it: a declared `Content-Length` over the limit
is refused before a byte is read, and a body sent without one (chunked) is counted as it
streams and refused as soon as it passes the limit. Both answer 413 `IMPORT_TOO_LARGE`, the
code of a file that is too large once decoded.
"""

from collections.abc import Callable

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from portfolio_tracker.api.errors import error_response
from portfolio_tracker.importers.base import MAX_BASE64_CHARS, MAX_FILE_BYTES

# The base64 text plus room for the other fields (file name, choices, up to 20,000 selected
# line keys of about 15 characters each).
MAX_IMPORT_BODY = MAX_BASE64_CHARS + 1024 * 1024
IMPORT_UPLOADS = frozenset({"/api/v1/imports/preview", "/api/v1/imports/confirm"})


class BodyTooLarge(HTTPException):
    """Raised from `receive` while the app reads the body: FastAPI re-raises HTTPExceptions
    from body parsing (any other exception would become a 400)."""

    def __init__(self) -> None:
        super().__init__(status_code=413)


def too_large_response():
    return error_response(
        413, "IMPORT_TOO_LARGE", f"the file is larger than {MAX_FILE_BYTES} bytes"
    )


async def body_too_large(_request, _exc: BodyTooLarge):
    return too_large_response()


class BodyLimit:
    def __init__(self, app: ASGIApp, limit_for: Callable[[str], int | None]) -> None:
        self.app = app
        self.limit_for = limit_for

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = self.limit_for(scope["path"]) if scope["type"] == "http" else None
        if limit is None:
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await too_large_response()(scope, receive, send)  # the body is never read
            return
        received = 0

        async def bounded() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge
            return message

        await self.app(scope, bounded, send)


def import_body_limit(path: str) -> int | None:
    return MAX_IMPORT_BODY if path in IMPORT_UPLOADS else None


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
