"""Live intraday board — reads the per-minute indicator_live snapshot (VWAP, ORB, rvol, structure).

This is a LIVE READ of the intraday signals, not a validated edge: it surfaces where price sits vs
VWAP and the opening range, with relative volume and 5m structure, plus a simple confluence bias.
"""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ...webcore.db import DatabaseUnavailable, open_ro
from ...webcore.introspect import table_exists

router = APIRouter(prefix="/api/intraday")

_COLS = ("symbol", "updated_at", "ltp", "vwap", "price_vs_vwap", "orb_high", "orb_low",
         "orb_break", "rvol_5m", "structure_5m", "supertrend_5m_dir", "rsi_5m", "vol_delta",
         "momentum_state")


def _get_conn() -> Iterator:
    try:
        conn = open_ro()
    except DatabaseUnavailable as e:
        raise HTTPException(503, str(e))
    try:
        yield conn
    finally:
        conn.close()


def _up(v) -> bool:   # supertrend dir is stored as 1/-1 or 'up'/'down' across versions
    return v in (1, 1.0, "up", "bull", "long")


def _down(v) -> bool:
    return v in (-1, -1.0, "down", "bear", "short")


@router.get("")
def latest(conn=Depends(_get_conn)) -> JSONResponse:
    if not table_exists(conn, "indicator_live"):
        raise HTTPException(503, "indicator_live not present")
    rows = conn.execute(
        f"SELECT {', '.join(_COLS)} FROM indicator_live WHERE ltp IS NOT NULL AND vwap IS NOT NULL "
        "ORDER BY (rvol_5m IS NULL), rvol_5m DESC").fetchall()
    out = []
    for r in rows:
        d = dict(zip(_COLS, r))
        ob = d.get("orb_break")
        d["orb_state"] = ("above" if ob == 1 else "below" if ob == -1 else "inside"
                          if ob == 0 else None)
        d["vs_vwap_pct"] = (round((d["ltp"] - d["vwap"]) / d["vwap"] * 100, 2)
                            if d["ltp"] and d["vwap"] else None)
        # simple confluence bias (VWAP side + ORB break + 5m supertrend) — a read, not a signal
        longs = (d["price_vs_vwap"] == "above") + (ob == 1) + _up(d["supertrend_5m_dir"])
        shorts = (d["price_vs_vwap"] == "below") + (ob == -1) + _down(d["supertrend_5m_dir"])
        d["bias"] = "LONG" if longs > shorts else "SHORT" if shorts > longs else "—"
        d["confluence"] = max(longs, shorts)
        out.append(d)
    asof = max((d["updated_at"] for d in out), default=None)
    return JSONResponse({"as_of": asof, "count": len(out), "rows": out})
