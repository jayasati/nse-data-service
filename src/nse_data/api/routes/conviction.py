"""Conviction read API — the persisted pre-market 13-stage synthesis (conviction_daily)."""
from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ...webcore.db import DatabaseUnavailable, open_ro
from ...webcore.introspect import table_exists

router = APIRouter(prefix="/api/conviction")


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
    if not table_exists(conn, "conviction_daily"):
        raise HTTPException(503, "conviction_daily not present (engine not yet run)")
    d = conn.execute("SELECT MAX(as_of_date) FROM conviction_daily").fetchone()[0]
    if not d:
        raise HTTPException(404, "no conviction snapshot yet")
    mac = conn.execute("SELECT macro_json, smart_money FROM conviction_macro WHERE as_of_date=?",
                       (d,)).fetchone()
    rows = conn.execute(
        "SELECT symbol, composite, tier, catalyst, positioning, options, structure, volume, "
        "rel_strength, vol_expansion, data_gaps, direction, entry, stop, t1, t2, t3, rr, setup, "
        "probability, stages_json FROM conviction_daily "
        "WHERE as_of_date=? ORDER BY composite DESC", (d,)).fetchall()
    cols = ["symbol", "composite", "tier", "catalyst", "positioning", "options", "structure",
            "volume", "rel_strength", "vol_expansion", "data_gaps", "direction", "entry", "stop",
            "t1", "t2", "t3", "rr", "setup", "probability", "stages_json"]
    out = []
    for r in rows:
        row = dict(zip(cols, r))
        row["stages"] = json.loads(row.pop("stages_json")) if row.get("stages_json") else {}
        out.append(row)
    return JSONResponse({"date": d, "macro": json.loads(mac[0]) if mac else None,
                         "smart_money": mac[1] if mac else None, "count": len(out), "rows": out})
