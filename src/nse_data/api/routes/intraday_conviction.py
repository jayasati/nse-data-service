"""Intraday conviction read API — ranks the live `indicator_live` stack into a momentum scanner.
Computed on-demand (indicator_live is a small per-symbol snapshot, no candle re-reads), so it always
reflects the latest minute. See research/intraday_conviction for the honest no-validated-edge framing.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ...research.intraday_conviction import run_intraday_conviction
from ...scheduler.market_hours import IST
from ...webcore.db import DatabaseUnavailable, open_ro

router = APIRouter(prefix="/api/intraday-conviction")


def _get_conn() -> Iterator:
    try:
        conn = open_ro()
    except DatabaseUnavailable as e:
        raise HTTPException(503, str(e))
    try:
        yield conn
    finally:
        conn.close()


@router.get("")
def latest(conn=Depends(_get_conn)) -> JSONResponse:
    rows = run_intraday_conviction(conn)
    now = dt.datetime.now(IST)
    mkt = now.weekday() < 5 and dt.time(9, 15) <= now.time() <= dt.time(15, 30)
    fresh = conn.execute("SELECT MAX(updated_at) FROM indicator_live").fetchone()[0]
    return JSONResponse({
        "rows": rows, "count": len(rows), "market_open": mkt,
        "as_of": now.strftime("%H:%M:%S"), "live_updated": fresh,
        "longs": sum(1 for r in rows if r["direction"] == "LONG"),
        "shorts": sum(1 for r in rows if r["direction"] == "SHORT")})
