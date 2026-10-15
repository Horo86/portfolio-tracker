"""Request-scoped dependencies."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from portfolio_tracker.db import BEGIN_OPTION

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_session(request: Request) -> Iterator[Session]:
    """One database transaction per request, committed when the handler returns.

    Write requests start with BEGIN IMMEDIATE: validation and persistence run under the
    write lock, so two concurrent changes to a ledger cannot both pass validation.
    """
    mode = "DEFERRED" if request.method in _READ_METHODS else "IMMEDIATE"
    bind = request.app.state.engine.execution_options(**{BEGIN_OPTION: mode})
    with Session(bind, expire_on_commit=False) as session, session.begin():
        yield session


DbSession = Annotated[Session, Depends(get_session)]
