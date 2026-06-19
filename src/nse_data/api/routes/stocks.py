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
            end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
            svc: StockService = Depends(get_service)) -> JSONResponse:
    # `end` (YYYY-MM-DD, IST) anchors the right edge to a past date for
    # point-in-time verification; None means "up to now" (live).
    return _run(lambda: svc.history(symbol, interval, days, end=end))


@router.get("/{symbol}/score-history")
def score_history(symbol: str, days: int = Query(365, ge=1, le=3000),
                  svc: StockService = Depends(get_service)) -> JSONResponse:
    # Daily ranking-engine composite + factor scores for the chart-view overlay.
    return _run(lambda: svc.score_history(symbol, days))


@router.get("/{symbol}/indicators")
def indicators(symbol: str,
               days: int = Query(365, ge=1, le=6000),
               cadence: str | None = Query(None, pattern="^(eod|intraday|session)$"),
               end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
               svc: StockService = Depends(get_service)) -> JSONResponse:
    # `days` is repurposed as a row cap. For EOD indicators it's days of
    # history; for intraday (5-min bars) it's interpreted as "the latest N
    # rows", which the frontend translates from its timeframe.
    # `end` (YYYY-MM-DD, IST) caps rows at a past date so overlays match a
    # point-in-time history view.
    return _run(lambda: svc.indicators(symbol, days, cadence=cadence, end=end))


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


@router.get("/{symbol}/moves")
def moves(symbol: str, svc: StockPageService = Depends(get_page_service)) -> JSONResponse:
    return _run(lambda: svc.moves(symbol))
