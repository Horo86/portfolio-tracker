"""Security headers on every response (R-16) and the handler of unexpected errors (D-163).

Both are plain ASGI middleware, so they also cover the built frontend, the 404s of the static
files and the errors that no exception handler knows.
"""

import logging

from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

# The strict policy of D-163: the build has no inline script or style (M5), the fonts are
# bundled, and the app talks only to its own origin. No HSTS and no upgrade-insecure-requests:
# TLS is the reverse proxy's job, and plain http on localhost must keep working.
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
# Only with PT_API_DOCS=true, only on the interactive docs page: FastAPI's Swagger UI loads its
# script and style from jsDelivr, starts with an inline script and shows FastAPI's favicon.
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
DOCS_PATHS = frozenset({"/api/docs", "/api/docs/oauth2-redirect"})

_FIXED = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"cross-origin-opener-policy", b"same-origin"),
]


class SecurityHeaders:
    def __init__(self, app: ASGIApp, *, docs: bool) -> None:
        self.app = app
        self.docs = docs

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        csp = DOCS_CSP if self.docs and scope["path"] in DOCS_PATHS else CSP
        added = [(b"content-security-policy", csp.encode()), *_FIXED]
        names = {name for name, _ in added}

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [h for h in message.get("headers", []) if h[0].lower() not in names]
                message = {**message, "headers": headers + added}
            await send(message)

        await self.app(scope, receive, with_headers)


class UnexpectedErrors:
    """An error no handler knows becomes 500 `INTERNAL_ERROR` with no detail. The log gets
    the exception type and the route template only: never the message, a traceback, the
    path's values, the headers or the body, which may quote figures (D-114, D-163)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def watched(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, watched)
        except HTTPException:
            raise
        except Exception as exc:
            route = getattr(scope.get("route"), "path", None) or "(no route)"
            log.error(
                "unexpected error",
                extra={"exception": type(exc).__name__, "route": route, "method": scope["method"]},
            )
            if not started:
                response = JSONResponse(
                    status_code=500, content={"detail": "internal error", "code": "INTERNAL_ERROR"}
                )
                await response(scope, receive, send)
