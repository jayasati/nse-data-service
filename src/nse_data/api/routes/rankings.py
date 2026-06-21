"""Weekly-ranking read API — thin HTTP layer over RankingsService.

/api/rankings/weeks → the available weeks; /api/rankings/week/{date} → that week's ranked table
(all candidates + the selected basket). Mirrors api/routes/strategy_analytics.py.
"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ...webcore.db import DatabaseUnavailable, open_ro
from ...webcore.errors import BadRequest, NotFound, Unavailable
from ...webcore.repositories.rankings import RankingsRepository
from ...webcore.services.rankings import RankingsService

router = APIRouter(prefix="/api/rankings")


def _get_conn() -> Iterator:
    try:
        conn = open_ro()
    except DatabaseUnavailable as e:
        raise HTTPException(503, str(e))
    try:
        yield conn
    finally:
        conn.close()


def _get_service(conn=Depends(_get_conn)) -> RankingsService:
    return RankingsService(RankingsRepository(conn))


def _run(call):
    try:
        return JSONResponse(call())
    except BadRequest as e:
        raise HTTPException(400, str(e))
    except NotFound as e:
        raise HTTPException(404, str(e))
    except Unavailable as e:
        raise HTTPException(503, str(e))


@router.get("/weeks")
def weeks(svc: RankingsService = Depends(_get_service)) -> JSONResponse:
    return _run(svc.weeks)


@router.get("/week/{week_date}")
def week(week_date: str, selected: bool = Query(False),
         svc: RankingsService = Depends(_get_service)) -> JSONResponse:
    return _run(lambda: svc.week(week_date, selected_only=selected))
