"""Stock data routes — thin HTTP layer over StockService.

Each handler delegates to the service and translates domain exceptions into
HTTP status codes. No business logic or SQL here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ..deps import get_page_service, get_service
from ...webcore.errors import BadRequest, NotFound, Unavailable
from ...webcore.services.stock_page import StockPageService
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


@router.get("/{symbol}/indicators")
def indicators(symbol: str,
               days: int = Query(365, ge=1, le=6000),
               cadence: str | None = Query(None, pattern="^(eod|intraday|session)$"),
               svc: StockService = Depends(get_service)) -> JSONResponse:
    # `days` is repurposed as a row cap. For EOD indicators it's days of
    # history; for intraday (5-min bars) it's interpreted as "the latest N
    # rows", which the frontend translates from its timeframe.
    return _run(lambda: svc.indicators(symbol, days, cadence=cadence))


@router.get("/{symbol}/meta")
def meta(symbol: str, svc: StockService = Depends(get_service)) -> JSONResponse:
    return _run(lambda: svc.meta(symbol))


# ---- the stock-cockpit tabs (StockPageService; every section is read-only and
# renders empty rather than erroring when its tables have no rows) ------------

@router.get("/{symbol}/overview")
def overview(symbol: str, svc: StockPageService = Depends(get_page_service)) -> JSONResponse:
    return _run(lambda: svc.overview(symbol))


@router.get("/{symbol}/results")
def results(symbol: str, svc: StockPageService = Depends(get_page_service)) -> JSONResponse:
    return _run(lambda: svc.results(symbol))


@router.get("/{symbol}/events")
def events(symbol: str, svc: StockPageService = Depends(get_page_service)) -> JSONResponse:
    return _run(lambda: svc.events(symbol))


@router.get("/{symbol}/filings")
def filings(symbol: str, svc: StockPageService = Depends(get_page_service)) -> JSONResponse:
    return _run(lambda: svc.filings(symbol))


@router.get("/{symbol}/activity")
def activity(symbol: str, svc: StockPageService = Depends(get_page_service)) -> JSONResponse:
    return _run(lambda: svc.activity(symbol))


@router.get("/{symbol}/flow")
def flow(symbol: str, svc: StockPageService = Depends(get_page_service)) -> JSONResponse:
    return _run(lambda: svc.flow(symbol))
