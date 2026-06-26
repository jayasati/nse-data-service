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


@router.get("/api/health/paper_loop")
def api_health_paper_loop(conn=Depends(get_conn)) -> JSONResponse:
    """Liveness of the 19:15 paper-trade loop — the forward-validation heartbeat.

    gate_status is always OPEN: paper trades are SIMULATED and are never blocked by the
    live-P&L (G12) gate — that gate applies to live order dispatch only. So a stall here is
    a job/data fault, never a gate trip.
    """
    def _scalar(sql, args=()):
        r = conn.execute(sql, args).fetchone()
        return r[0] if r else None

    hb = conn.execute(
        "SELECT last_run_utc, status, detail, items_booked, consecutive_zero, updated_at "
        "FROM job_heartbeat WHERE job_id='paper_trade'").fetchone() \
        if _scalar("SELECT name FROM sqlite_master WHERE type='table' AND name='job_heartbeat'") \
        else None

    last_booked = _scalar("SELECT MAX(updated_at) FROM paper_book")
    if isinstance(last_booked, (int, float)) or (isinstance(last_booked, str) and last_booked.isdigit()):
        import datetime as _dt
        last_booked = _dt.datetime.fromtimestamp(int(last_booked), _dt.timezone.utc).isoformat()
    trades_7d = _scalar(
        "SELECT COUNT(*) FROM paper_book WHERE entry_date >= date('now','-7 day')") or 0
    by_strategy = {
        k: v for k, v in conn.execute(
            "SELECT strategy, COUNT(*) FROM paper_book "
            "WHERE entry_date >= date('now','-7 day') GROUP BY strategy")}
    consecutive_zero = hb[4] if hb else None

    return JSONResponse({
        "job_id": "paper_trade",
        "last_job_run_utc": hb[0] if hb else None,
        "last_run_status": hb[1] if hb else None,
        "last_run_detail": hb[2] if hb else None,
        "last_run_items_booked": hb[3] if hb else None,
        "last_paper_trade_booked_utc": last_booked,
        "trades_last_7d": trades_7d,
        "trades_last_7d_by_strategy": by_strategy,
        "consecutive_zero_sessions": consecutive_zero,
        "gate_status": "OPEN",
        "gate_inputs": {"note": "paper recording is never gated by live P&L; G12 applies to "
                                "live order dispatch only"},
        "healthy": bool(hb) and hb[1] != "failed" and (consecutive_zero or 0) < 2,
    })


@router.get("/api/table/{name}")
def api_table(name: str, limit: int = Query(50, ge=1, le=500),
              repo: StockRepository = Depends(get_repo)) -> JSONResponse:
    if not repo.has_table(name):
        raise HTTPException(404, f"no such table: {name}")
    order_by, rows = repo.table_recent(name, limit)
    return JSONResponse({"table": name, "order_by": order_by,
                         "count": len(rows), "rows": rows})
