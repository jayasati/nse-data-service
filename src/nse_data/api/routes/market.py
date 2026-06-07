"""
Market-context routes — regime + sector radar (Phase 2, Weeks 7–8).

Exposes the latest `market_state` (overall regime) and `sector_state` (the
sector RS leaderboard) for the dashboard. Both read the most recent snapshot and
degrade gracefully on a pre-Phase-2 DB (table absent → empty payload, not 500).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..deps import get_conn

router = APIRouter()


def _rows_as_dicts(conn, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


@router.get("/api/market/regime")
def api_market_regime(conn=Depends(get_conn)) -> JSONResponse:
    """Latest market regime snapshot, or {regime: null} if none yet."""
    try:
        rows = _rows_as_dicts(
            conn, "SELECT * FROM market_state ORDER BY as_of DESC LIMIT 1"
        )
    except sqlite3.OperationalError:
        rows = []
    return JSONResponse({"regime": rows[0] if rows else None})


@router.get("/api/market/sectors")
def api_market_sectors(conn=Depends(get_conn)) -> JSONResponse:
    """Latest sector RS leaderboard (11 sectors, best rank first)."""
    try:
        rows = _rows_as_dicts(
            conn,
            "SELECT * FROM sector_state WHERE as_of = "
            "(SELECT MAX(as_of) FROM sector_state) ORDER BY rs_rank",
        )
    except sqlite3.OperationalError:
        rows = []
    return JSONResponse({
        "as_of": rows[0]["as_of"] if rows else None,
        "count": len(rows),
        "sectors": rows,
    })
