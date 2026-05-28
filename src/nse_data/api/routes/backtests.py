"""Backtest read API — thin HTTP layer over BacktestService.

Mirrors api/routes/stocks.py: same error mapping, same lazy DB wiring via
Depends, no business logic here.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ...webcore.db import DatabaseUnavailable, open_ro
from ...webcore.errors import BadRequest, NotFound, Unavailable
from ...webcore.repositories.backtests import BacktestRepository
from ...webcore.services.backtests import BacktestService

router = APIRouter(prefix="/api/backtests")


def _get_conn() -> Iterator:
    try:
        conn = open_ro()
    except DatabaseUnavailable as e:
        raise HTTPException(503, str(e))
    try:
        yield conn
    finally:
        conn.close()


def _get_service(conn=Depends(_get_conn)) -> BacktestService:
    return BacktestService(BacktestRepository(conn))


def _run(call):
    try:
        return JSONResponse(call())
    except BadRequest as e:
        raise HTTPException(400, str(e))
    except NotFound as e:
        raise HTTPException(404, str(e))
    except Unavailable as e:
        raise HTTPException(503, str(e))


@router.get("/runs")
def list_runs(limit: int = Query(20, ge=1, le=200),
              svc: BacktestService = Depends(_get_service)) -> JSONResponse:
    return _run(lambda: svc.list_runs(limit))


@router.get("/runs/{run_id}")
def get_run(run_id: int,
            svc: BacktestService = Depends(_get_service)) -> JSONResponse:
    return _run(lambda: svc.get_run(run_id))


@router.get("/runs/{run_id}/trades")
def trades(run_id: int,
           symbol: str | None = Query(None, max_length=40),
           limit: int = Query(100, ge=1, le=1000),
           offset: int = Query(0, ge=0),
           svc: BacktestService = Depends(_get_service)) -> JSONResponse:
    return _run(lambda: svc.trades(run_id, symbol=symbol, limit=limit, offset=offset))


@router.get("/runs/{run_id}/by-symbol")
def by_symbol(run_id: int,
              svc: BacktestService = Depends(_get_service)) -> JSONResponse:
    return _run(lambda: svc.by_symbol(run_id))
