"""Monitoring routes — collector-health report + raw-table preview.

The HTTP surface for observability. The freshness reasoning itself lives in the
framework-free ops.health domain module; this layer just exposes it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ...ops import gates, health
from ...settings import load_endpoints
from ...webcore.config import ENDPOINTS_PATH
from ...webcore.repositories.stocks import StockRepository
from ..deps import get_conn, get_repo

router = APIRouter()


@router.get("/api/health")
def api_health(conn=Depends(get_conn)) -> JSONResponse:
    endpoints = load_endpoints(ENDPOINTS_PATH)
    return JSONResponse(health.build_report(conn, endpoints))


@router.get("/api/gates")
def api_gates(conn=Depends(get_conn)) -> JSONResponse:
    return JSONResponse(gates.build_report(conn))


@router.get("/api/table/{name}")
def api_table(name: str, limit: int = Query(50, ge=1, le=500),
              repo: StockRepository = Depends(get_repo)) -> JSONResponse:
    if not repo.has_table(name):
        raise HTTPException(404, f"no such table: {name}")
    order_by, rows = repo.table_recent(name, limit)
    return JSONResponse({"table": name, "order_by": order_by,
                         "count": len(rows), "rows": rows})
