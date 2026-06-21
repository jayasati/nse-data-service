"""Stock-research read API — one assembled payload per symbol + date range."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ...webcore.db import DatabaseUnavailable, open_ro
from ...webcore.errors import BadRequest, NotFound, Unavailable
from ...webcore.repositories.research import ResearchRepository
from ...webcore.services.research import ResearchService

router = APIRouter(prefix="/api/research")


def _get_conn() -> Iterator:
    try:
        conn = open_ro()
    except DatabaseUnavailable as e:
        raise HTTPException(503, str(e))
    try:
        yield conn
    finally:
        conn.close()


def _get_service(conn=Depends(_get_conn)) -> ResearchService:
    return ResearchService(ResearchRepository(conn))


@router.get("/{symbol}")
def study(symbol: str, start: str = Query("2026-04-01"), end: str = Query("2026-06-19"),
          svc: ResearchService = Depends(_get_service)) -> JSONResponse:
    try:
        return JSONResponse(svc.study(symbol, start, end))
    except BadRequest as e:
        raise HTTPException(400, str(e))
    except NotFound as e:
        raise HTTPException(404, str(e))
    except Unavailable as e:
        raise HTTPException(503, str(e))
