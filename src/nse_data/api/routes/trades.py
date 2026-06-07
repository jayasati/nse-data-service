"""Paper-trades read API — thin HTTP layer over TradesService.

Mirrors api/routes/backtests.py: same error mapping, same lazy DB wiring via
Depends, no business logic here.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ...webcore.db import DatabaseUnavailable, open_ro
from ...webcore.errors import BadRequest, NotFound, Unavailable
from ...webcore.repositories.trades import TradesRepository
from ...webcore.services.trades import TradesService

router = APIRouter(prefix="/api/trades")


def _get_conn() -> Iterator:
    try:
        conn = open_ro()
    except DatabaseUnavailable as e:
        raise HTTPException(503, str(e))
    try:
        yield conn
    finally:
        conn.close()


def _get_service(conn=Depends(_get_conn)) -> TradesService:
    return TradesService(TradesRepository(conn))


def _run(call):
    try:
        return JSONResponse(call())
    except BadRequest as e:
        raise HTTPException(400, str(e))
    except NotFound as e:
        raise HTTPException(404, str(e))
    except Unavailable as e:
        raise HTTPException(503, str(e))


@router.get("/overview")
def overview(svc: TradesService = Depends(_get_service)) -> JSONResponse:
    return _run(svc.overview)


@router.get("/by-strategy")
def by_strategy(svc: TradesService = Depends(_get_service)) -> JSONResponse:
    return _run(svc.by_strategy)


@router.get("")
def list_trades(
    status: str | None = Query(None, pattern="^(open|closed)$"),
    strategy: str | None = Query(None, max_length=40),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    svc: TradesService = Depends(_get_service),
) -> JSONResponse:
    return _run(lambda: svc.list_trades(
        status=status, strategy=strategy, limit=limit, offset=offset,
    ))
