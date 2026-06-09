"""Earnings-engine read API — thin HTTP layer over EarningsService.

Mirrors api/routes/trades.py: lazy read-only DB via Depends, error mapping, no
business logic. Surfaces the engine's probability/accuracy and recent reactions
to the dashboard.
"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ...webcore.db import DatabaseUnavailable, open_ro
from ...webcore.errors import BadRequest, NotFound, Unavailable
from ...webcore.repositories.earnings import EarningsRepository
from ...webcore.services.earnings import EarningsService

router = APIRouter(prefix="/api/earnings")


def _get_conn() -> Iterator:
    try:
        conn = open_ro()
    except DatabaseUnavailable as e:
        raise HTTPException(503, str(e))
    try:
        yield conn
    finally:
        conn.close()


def _get_service(conn=Depends(_get_conn)) -> EarningsService:
    return EarningsService(EarningsRepository(conn))


def _run(call):
    try:
        return JSONResponse(call())
    except BadRequest as e:
        raise HTTPException(400, str(e))
    except NotFound as e:
        raise HTTPException(404, str(e))
    except Unavailable as e:
        raise HTTPException(503, str(e))


@router.get("/odds")
def odds(svc: EarningsService = Depends(_get_service)) -> JSONResponse:
    return _run(svc.odds)


@router.get("/reactions")
def reactions(
    direction: str | None = Query(None, pattern="^(long|short)$"),
    limit: int = Query(200, ge=1, le=1000),
    svc: EarningsService = Depends(_get_service),
) -> JSONResponse:
    return _run(lambda: svc.reactions(direction=direction, limit=limit))


@router.get("/upcoming")
def upcoming(
    limit: int = Query(200, ge=1, le=1000),
    svc: EarningsService = Depends(_get_service),
) -> JSONResponse:
    return _run(lambda: svc.upcoming(limit=limit))
