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


@router.get("/api/health/all_collectors")
def api_health_all_collectors(conn=Depends(get_conn)) -> JSONResponse:
    """Collector freshness across the board — same domain report as /api/health, surfaced under
    the spec's expected path."""
    endpoints = load_endpoints(ENDPOINTS_PATH)
    return JSONResponse(health.build_report(conn, endpoints))


@router.get("/api/health/signals_today")
def api_health_signals_today(conn=Depends(get_conn)) -> JSONResponse:
    """Everything the signal layers flagged today, in one place. Defensive — a table that
    doesn't exist yet (e.g. before P2/P3 land) just yields an empty list, never a 500."""
    def _rows(sql, args=()):
        try:
            cur = conn.execute(sql, args)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:  # noqa: BLE001 — table missing / schema drift → empty
            return []

    import datetime as _dt
    today = _dt.date.today().isoformat()
    return JSONResponse({
        "date": today,
        "basket_rotation": _rows(
            "SELECT basket_name, signal_type, confidence, advancing, declining "
            "FROM basket_signals WHERE signal_date=? ORDER BY confidence DESC", (today,)),
        "events_upcoming_3d": _rows(
            "SELECT symbol, event_type, expected_date, confidence FROM pending_events "
            "WHERE status='upcoming' AND expected_date>? AND expected_date<=date(?, '+3 day') "
            "ORDER BY expected_date LIMIT 30", (today, today)),
        "smallcap_signals": _rows(
            "SELECT symbol, move_pct, vol_ratio, is_52w_breakout, signal FROM smallcap_signals "
            "WHERE signal_date=? AND signal IS NOT NULL ORDER BY move_pct DESC", (today,)),
        "options_notable": _rows(
            "SELECT symbol, pcr, max_pain, gex_sign FROM options_metrics "
            "WHERE as_of>=strftime('%s',?) ORDER BY symbol LIMIT 30", (today,)),
        "large_deal_signals": _rows(            # P2 — empty until built
            "SELECT symbol, entity_type, txn_type, value_cr, signal_type FROM large_deal_signals "
            "WHERE deal_date=? AND signal_type IS NOT NULL ORDER BY value_cr DESC", (today,)),
        "promoter_signals": _rows(              # P3 — empty until built
            "SELECT symbol, signal_type, holding_change_pct, horizon_days FROM promoter_signals "
            "WHERE filing_date=? AND signal_type NOT IN ('NEUTRAL','SKIP')", (today,)),
        "universe_gaps": _rows(
            "SELECT symbol, move_pct, reason_out FROM universe_gaps WHERE gap_date=? "
            "ORDER BY ABS(move_pct) DESC LIMIT 20", (today,)),
    })


@router.get("/api/table/{name}")
def api_table(name: str, limit: int = Query(50, ge=1, le=500),
              repo: StockRepository = Depends(get_repo)) -> JSONResponse:
    if not repo.has_table(name):
        raise HTTPException(404, f"no such table: {name}")
    order_by, rows = repo.table_recent(name, limit)
    return JSONResponse({"table": name, "order_by": order_by,
                         "count": len(rows), "rows": rows})
