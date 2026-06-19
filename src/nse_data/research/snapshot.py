"""P8 feature store — compute one daily factor snapshot per stock and persist it.

Runs every factor engine over the universe as-of a date, assembles the validated
composite + sector rank + market regime, and UPSERTs into `factor_snapshot`
(migration 082). Forward-return labels are filled later by scripts/label_snapshots.py
once each horizon matures — this module only writes the point-in-time features.

Re-running for the same date overwrites the FEATURE columns but preserves any
already-filled fwd_excess_* labels (ON CONFLICT … DO UPDATE, labels untouched).
"""
from __future__ import annotations

from . import (
    catalyst_engine, composite_engine, confidence_engine, liquidity_engine,
    momentum_engine, ownership_engine, quality_engine, risk_engine, surprise_engine,
    turnaround_engine, valuation_engine,
)

# (column, engine) — fixed order = the snapshot's feature layout. All expose
# score_universe(conn, symbols, as_of_ep, sector_of) -> {sym: {'score',...}}.
ENGINES = (
    ("quality", quality_engine), ("valuation", valuation_engine),
    ("momentum", momentum_engine), ("turnaround", turnaround_engine),
    ("liquidity", liquidity_engine), ("surprise", surprise_engine),
    ("catalyst", catalyst_engine), ("ownership", ownership_engine),
    ("risk", risk_engine), ("confidence", confidence_engine),
)
_COLS = [c for c, _ in ENGINES]


def compute_snapshot(conn, symbols, as_of_ep, sector_of) -> dict:
    """{symbol: {sector, <each engine score or None>, composite, sector_rank, sector_n}}
    for every name at least one engine scored. Composite = validated Q+V engine."""
    scored = {name: mod.score_universe(conn, symbols, as_of_ep, sector_of)
              for name, mod in ENGINES}
    comp = composite_engine.score_universe(conn, symbols, as_of_ep, sector_of)
    rows: dict[str, dict] = {}
    for s in symbols:
        feat = {name: scored[name].get(s, {}).get("score") for name in _COLS}
        c = comp.get(s, {}).get("score")
        if c is None and all(v is None for v in feat.values()):
            continue                                   # nothing known → skip
        rows[s] = {"sector": sector_of(s), "composite": c,
                   "sector_rank": None, "sector_n": None, **feat}
    # sector rank by composite (1 = best in sector on this date)
    by_sec: dict[str, list] = {}
    for s, r in rows.items():
        if r["composite"] is not None:
            by_sec.setdefault(r["sector"], []).append((s, r["composite"]))
    for lst in by_sec.values():
        lst.sort(key=lambda kv: -kv[1])
        n = len(lst)
        for rank, (s, _) in enumerate(lst, 1):
            rows[s]["sector_rank"] = rank
            rows[s]["sector_n"] = n
    return rows


def _regime(conn) -> str | None:
    try:
        from ..market.regime_job import latest_market_state
        st = latest_market_state(conn) or {}
        return st.get("overall_regime")
    except Exception:  # noqa: BLE001 — regime is optional context, never fatal
        return None


def persist_snapshot(conn, snapshot_date: str, computed_epoch: int, rows: dict,
                     grade_of: dict, regime: str | None) -> int:
    """UPSERT feature columns for the date; leaves fwd_excess_* labels intact."""
    cols = (["snapshot_date", "symbol", "sector", "grade"] + _COLS
            + ["composite", "sector_rank", "sector_n", "regime", "computed_epoch"])
    placeholders = ",".join("?" * len(cols))
    upd = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("snapshot_date", "symbol"))
    sql = (f"INSERT INTO factor_snapshot ({','.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT(snapshot_date, symbol) DO UPDATE SET {upd}")
    n = 0
    for s, r in rows.items():
        vals = ([snapshot_date, s, r["sector"], grade_of.get(s)]
                + [r[c] for c in _COLS]
                + [r["composite"], r["sector_rank"], r["sector_n"], regime, computed_epoch])
        conn.execute(sql, vals)
        n += 1
    conn.commit()
    return n


def run_snapshot(conn, snapshot_date: str, computed_epoch: int, as_of_ep: int,
                 grades=("A core", "B tradeable", "C volatile")) -> int:
    """End-to-end: pick the graded universe, compute, persist. Returns rows written."""
    from ..fundamentals.sectors import sector_class_for
    grade_of = {r[0]: r[1] for r in conn.execute("SELECT symbol, grade FROM tradeable_universe")}
    symbols = [s for s, g in grade_of.items() if g in grades]
    sec_cache: dict[str, str] = {}
    def sector_of(s):
        if s not in sec_cache:
            sec_cache[s] = sector_class_for(s).value
        return sec_cache[s]
    rows = compute_snapshot(conn, symbols, as_of_ep, sector_of)
    return persist_snapshot(conn, snapshot_date, computed_epoch, rows, grade_of, _regime(conn))


LABEL_HZ = (30, 60, 90, 120)


def label_matured(conn, cost: float = 0.50) -> dict:
    """Fill NULL fwd_excess_{30,60,90,120} on matured snapshot rows with realised
    sector-excess (net-of-cost) — same definition the backtests use. Idempotent:
    only writes a horizon once enough forward candles exist. Returns per-horizon count."""
    from ..fundamentals.sectors import sector_class_for
    from . import edge_stats

    bench_for, bseries, _ = edge_stats.bench_resolver(conn)
    sec_cache: dict[str, str] = {}
    def sector_of(s):
        if s not in sec_cache:
            sec_cache[s] = sector_class_for(s).value
        return sec_cache[s]

    where = " OR ".join(f"fwd_excess_{h} IS NULL" for h in LABEL_HZ)
    todo: dict[str, list] = {}
    for sym, d in conn.execute(
            f"SELECT symbol, snapshot_date FROM factor_snapshot WHERE {where}"):
        todo.setdefault(sym, []).append(d)

    filled = {h: 0 for h in LABEL_HZ}
    for sym, dates_needed in todo.items():
        series, sdates = edge_stats.load_series(conn, sym)
        if not sdates:
            continue
        bser, bdates = bseries[bench_for(sector_of(sym))]
        for d in dates_needed:
            if d not in series:
                continue
            i = sdates.index(d)
            s0 = series[d]
            b0 = edge_stats.close_on_or_before(bser, bdates, d)
            vals = {}
            for h in LABEL_HZ:
                if i + h >= len(sdates):
                    continue
                xd = sdates[i + h]
                b1 = edge_stats.close_on_or_before(bser, bdates, xd)
                bench = (b1 / b0 - 1) * 100 if (b0 and b1) else 0.0
                vals[h] = (series[xd] / s0 - 1) * 100 - bench - cost
                filled[h] += 1
            if vals:
                setc = ", ".join(f"fwd_excess_{h}=?" for h in vals)
                conn.execute(f"UPDATE factor_snapshot SET {setc} "
                             "WHERE snapshot_date=? AND symbol=?",
                             [vals[h] for h in vals] + [d, sym])
    conn.commit()
    return filled
