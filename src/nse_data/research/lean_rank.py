"""Liquidity-gated ranking on the OOS-validated Lean score (Valuation + Surprise).
The lean factor skews small-cap/value, so a *tradeable* top-N must gate on the
tradeable_universe grades (A core / B tradeable) — illiquid micro-caps are excluded so
the list is executable. Point-in-time off the latest factor_snapshot.
"""
from __future__ import annotations

from .buy_score import lean_raw

TRADEABLE = ("A core", "B tradeable")


def rank(conn, grades=TRADEABLE, limit: int = 50, as_of: str | None = None) -> list[dict]:
    """Top tradeable names by Lean score. `grades` filters tradeable_universe.grade;
    pass a wider tuple (e.g. + 'C volatile') to loosen, or () to disable the gate."""
    as_of = as_of or conn.execute("SELECT MAX(snapshot_date) FROM factor_snapshot").fetchone()[0]
    if not as_of:
        return []
    grade = {s: (g, tv, mc) for s, g, tv, mc in conn.execute(
        "SELECT symbol, grade, med_turnover_cr, market_cap_cr FROM tradeable_universe")}
    out = []
    for sym, sec, val, sur, risk in conn.execute(
            "SELECT symbol, sector, valuation, surprise, risk FROM factor_snapshot "
            "WHERE snapshot_date=?", (as_of,)):
        s, parts = lean_raw({"valuation": val, "surprise": sur})
        g = grade.get(sym)
        if s is None or "valuation" not in parts:
            continue
        if grades and (not g or g[0] not in grades):
            continue
        out.append({
            "symbol": sym, "sector": sec, "grade": g[0] if g else None,
            "lean_score": s, "valuation": parts.get("valuation"),
            "surprise": parts.get("surprise"), "risk": risk,
            "turnover_cr": g[1] if g else None, "market_cap_cr": g[2] if g else None,
            "both_factors": len(parts) == 2})
    out.sort(key=lambda r: -r["lean_score"])
    return out[:limit] if limit else out
