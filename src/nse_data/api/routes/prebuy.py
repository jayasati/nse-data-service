"""Pre-buy synthesis card — /api/prebuy/{symbol}. The full signal picture on one stock before you
act; ?synthesize=1 adds the LLM read (opt-in to respect the $25/day cap)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from ...research.prebuy_card import format_block, gather, synthesize
from ..deps import get_conn

router = APIRouter()


@router.get("/api/prebuy/{symbol}")
def api_prebuy(symbol: str, synth: bool = Query(False, alias="synthesize"),
               conn=Depends(get_conn)) -> JSONResponse:
    if synth:
        return JSONResponse(synthesize(conn, symbol))
    data = gather(conn, symbol)
    block, n = format_block(data)
    return JSONResponse({"symbol": symbol.upper(), "n_signals": n, "block": block, "signals": data})
