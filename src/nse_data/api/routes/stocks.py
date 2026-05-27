"""Stock data routes — thin HTTP layer over StockService.

Each handler delegates to the service and translates domain exceptions into
HTTP status codes. No business logic or SQL here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ..deps import get_service
from ...webcore.errors import BadRequest, NotFound, Unavailable
from ...webcore.services.stocks import StockService

router = APIRouter(prefix="/api/stocks")


def _run(call):
    try:
        return JSONResponse(call())
    except BadRequest as e:
        raise HTTPException(400, str(e))
    except NotFound as e:
        raise HTTPException(404, str(e))
    except Unavailable as e:
        raise HTTPException(503, str(e))


@router.get("/top")
def top(limit: int = Query(1000, ge=1, le=2000),
        by: str = Query("turnover"), source: str = Query("auto"),
        svc: StockService = Depends(get_service)) -> JSONResponse:
    return _run(lambda: svc.top(by, source, limit))


@router.get("/search")
def search(q: str = Query("", max_length=40), limit: int = Query(20, ge=1, le=50),
           svc: StockService = Depends(get_service)) -> JSONResponse:
    return _run(lambda: svc.search(q, limit))


@router.get("/{symbol}/history")
def history(symbol: str, interval: str = Query("1d"),
            days: int = Query(365, ge=1, le=6000),
            svc: StockService = Depends(get_service)) -> JSONResponse:
    return _run(lambda: svc.history(symbol, interval, days))


@router.get("/{symbol}/meta")
def meta(symbol: str, svc: StockService = Depends(get_service)) -> JSONResponse:
    return _run(lambda: svc.meta(symbol))
