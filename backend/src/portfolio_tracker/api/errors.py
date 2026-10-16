"""One JSON error body for every API error: `{"detail": str, "code": str, ...extra}` (ADR-0004).

`code` is stable and machine-readable; the UI translates it. `detail` is an English hint for
developers and logs, never shown to users as is.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "NOT_AUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "REQUEST_INVALID",
    501: "NOT_IMPLEMENTED",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.extra = extra


def not_found(what: str = "resource") -> ApiError:
    """Missing, or belonging to another user: the two are indistinguishable (owner isolation)."""
    return ApiError(404, "NOT_FOUND", f"{what} not found")


def error_response(status: int, code: str, detail: str, **extra: Any) -> JSONResponse:
    """The error body as a returned response, for handlers that must commit something (an
    audit entry) and still answer with an error. Adds Retry-After when given."""
    headers = {"Retry-After": str(extra["retry_after"])} if "retry_after" in extra else None
    return JSONResponse(status_code=status, content=_body(code, detail, **extra), headers=headers)


def _body(code: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"detail": detail, "code": code, **extra}


async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status, exc.code, exc.detail, **exc.extra)


async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
    detail = exc.detail if isinstance(exc.detail, str) else code.lower()
    return JSONResponse(
        status_code=exc.status_code, content=_body(code, detail), headers=exc.headers
    )


async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"loc": list(error.get("loc", ())), "msg": error.get("msg", ""), "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422, content=_body("REQUEST_INVALID", "request is invalid", errors=errors)
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
