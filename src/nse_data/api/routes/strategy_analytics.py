"""Strategy-analytics read API — thin HTTP layer over StrategyAnalyticsService.

Generic across strategies: /strategies, /runs, /runs/{id}, /runs/{id}/trades, and the
parametric /runs/{id}/pnl?by=symbol|day|week|month|regime|direction|exit_reason drill-down.
Mirrors api/routes/backtests.py — same DI + error mapping, no business logic here.
"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ...webcore.db import DatabaseUnavailable, open_ro
from ...webcore.errors import BadRequest, NotFound, Unavailable
from ...webcore.repositories.strategy_analytics import StrategyAnalyticsRepository
from ...webcore.services.strategy_analytics import StrategyAnalyticsService

router = APIRouter(prefix="/api/strategy-analytics")


def _get_conn() -> Iterator:
    try:
        conn = open_ro()
    except DatabaseUnavailable as e:
        raise HTTPException(503, str(e))
    try:
        yield conn
    finally:
        conn.close()


def _get_service(conn=Depends(_get_conn)) -> StrategyAnalyticsService:
    return StrategyAnalyticsService(StrategyAnalyticsRepository(conn))


def _run(call):
    try:
        return JSONResponse(call())
    except BadRequest as e:
        raise HTTPException(400, str(e))
    except NotFound as e:
        raise HTTPException(404, str(e))
    except Unavailable as e:
        raise HTTPException(503, str(e))


@router.get("/strategies")
def strategies(svc: StrategyAnalyticsService = Depends(_get_service)) -> JSONResponse:
    return _run(svc.strategies)


@router.get("/runs")
def list_runs(limit: int = Query(20, ge=1, le=200),
              strategy: str | None = Query(None, max_length=40),
              svc: StrategyAnalyticsService = Depends(_get_service)) -> JSONResponse:
    return _run(lambda: svc.list_runs(limit, strategy))


@router.get("/runs/{run_id}")
def get_run(run_id: int, svc: StrategyAnalyticsService = Depends(_get_service)) -> JSONResponse:
    return _run(lambda: svc.get_run(run_id))


@router.get("/runs/{run_id}/trades")
def trades(run_id: int, symbol: str | None = Query(None, max_length=40),
           direction: str | None = Query(None, pattern="^(long|short)$"),
           exit_reason: str | None = Query(None, pattern="^(target|stop|time)$"),
           limit: int = Query(200, ge=1, le=2000), offset: int = Query(0, ge=0),
           svc: StrategyAnalyticsService = Depends(_get_service)) -> JSONResponse:
    return _run(lambda: svc.trades(run_id, symbol=symbol, direction=direction,
                                   exit_reason=exit_reason, limit=limit, offset=offset))


@router.get("/runs/{run_id}/pnl")
def pnl_by(run_id: int, by: str = Query("symbol", max_length=20),
           svc: StrategyAnalyticsService = Depends(_get_service)) -> JSONResponse:
    return _run(lambda: svc.pnl_by(run_id, by))
