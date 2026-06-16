"""Build the investable-universe table: which stocks are liquid enough to trade
and stable enough for confident (low-volatility) decisions.

Computes per symbol (from the last ~1y of daily candles + stock_fundamentals):
  * last_close, last_date, traded_days_1y          — is it actually trading?
  * med_turnover_cr  = median(close*volume) in ₹cr — LIQUIDITY (can you get in/out)
  * ann_vol_pct      = stdev(daily log ret)*√252   — VOLATILITY (decision noise)
  * atr_pct          = mean (high-low)/close, 14d   — typical intraday range
  * market_cap, quality_score, loss_making          — size/quality context

Decision grade (liquidity AND volatility AND not loss-making):
  A core      : turnover ≥ ₹25cr, ann_vol < 35%, not loss-making   (size in size, sleep at night)
  B tradeable : turnover ≥ ₹5cr,  ann_vol < 50%                    (fine with normal sizing)
  C volatile  : tradeable liquidity but ann_vol ≥ 50% or loss-making (size down / event-only)
  illiquid    : turnover < ₹5cr or < 200 traded days              (avoid / exit-risk)

Writes the DB table `tradeable_universe` (reusable by the engine) and
data/tradeable_universe.csv (human-readable, sorted best-first).

    PYTHONPATH=src .venv/bin/python scripts/build_tradeable_universe.py
"""
from __future__ import annotations

import csv
import sqlite3
import statistics

DB = "data/nse.db"
CSV_OUT = "data/tradeable_universe.csv"
WINDOW = 252          # ~1 trading year
ATR_WINDOW = 14


import re

# ETFs / index & commodity funds — tradeable instruments but NOT stocks; they
# have no fundamentals and (for liquid/debt ETFs) ~0 vol, so they'd masquerade
# as "low-vol core". Bucketed separately so the stock universe stays clean.
_ETF_RE = re.compile(
    r"BEES|ETF|LIQUID|GOLD|SILVER|NIFTY|SENSEX|GSEC|SDL|MON100|CPSE|MAFANG|"
    r"HNGSNG|IETF|BETF|MOMENTUM|MNC|PSUBN|CASH|MID150|SMALLCAP\d|TOP100")


def _is_etf(symbol: str, market_cap, ann_vol) -> bool:
    # name pattern, OR a fundamentals-less near-zero-vol instrument (debt ETF)
    return bool(_ETF_RE.search(symbol)) or (market_cap is None and ann_vol is not None and ann_vol < 5)


def _grade(turnover_cr, ann_vol, traded_days, loss_making) -> str:
    if turnover_cr is None or traded_days < 200 or turnover_cr < 5:
        return "illiquid"
    if turnover_cr >= 25 and ann_vol is not None and ann_vol < 35 and not loss_making:
        return "A core"
    if ann_vol is not None and ann_vol < 50 and not loss_making:
        return "B tradeable"
    return "C volatile"


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA busy_timeout=60000")   # coexist with the live collector's writes

    fund = {r[0]: r for r in conn.execute(
        "SELECT symbol, market_cap, quality_score, loss_making, roe, debt_equity "
        "FROM stock_fundamentals")}

    syms = [s for (s,) in conn.execute(
        "SELECT DISTINCT symbol FROM raw_intraday_candles WHERE interval='day'")]

    rows = []
    for sym in syms:
        bars = conn.execute(
            "SELECT high, low, close, volume FROM raw_intraday_candles "
            "WHERE symbol=? AND interval='day' ORDER BY ts DESC LIMIT ?",
            (sym, WINDOW),
        ).fetchall()
        bars = [(h, l, c, v) for (h, l, c, v) in bars if c]
        if len(bars) < 30:
            continue
        closes = [c for _, _, c, _ in bars]                       # newest first
        turnovers = [c * v / 1e7 for _, _, c, v in bars if v]     # ₹cr per day
        # daily log returns (reverse to chronological for clarity; sign irrelevant to stdev)
        rets = []
        for i in range(len(closes) - 1):
            a, b = closes[i], closes[i + 1]
            if a and b and b > 0:
                rets.append((a / b) - 1.0)
        ann_vol = (statistics.pstdev(rets) * (252 ** 0.5) * 100) if len(rets) > 5 else None
        atr_bars = bars[:ATR_WINDOW]
        atr_pct = (statistics.mean([(h - l) / c for h, l, c, _ in atr_bars if c]) * 100
                   if atr_bars else None)
        med_turn = statistics.median(turnovers) if turnovers else None

        f = fund.get(sym)
        mcap = f[1] if f else None
        qscore = f[2] if f else None
        loss = bool(f[3]) if f and f[3] is not None else False
        roe = f[4] if f else None
        de = f[5] if f else None

        grade = "etf" if _is_etf(sym, mcap, ann_vol) else _grade(med_turn, ann_vol, len(bars), loss)
        rows.append({
            "symbol": sym,
            "grade": grade,
            "last_close": round(closes[0], 2),
            "med_turnover_cr": round(med_turn, 2) if med_turn is not None else None,
            "ann_vol_pct": round(ann_vol, 1) if ann_vol is not None else None,
            "atr_pct": round(atr_pct, 2) if atr_pct is not None else None,
            "traded_days_1y": len(bars),
            "market_cap_cr": round(mcap, 0) if mcap else None,
            "quality_score": round(qscore, 1) if qscore is not None else None,
            "roe": round(roe, 1) if roe is not None else None,
            "debt_equity": round(de, 2) if de is not None else None,
            "loss_making": int(loss),
        })

    # persist DB table
    conn.execute("DROP TABLE IF EXISTS tradeable_universe")
    conn.execute("""
        CREATE TABLE tradeable_universe (
            symbol TEXT PRIMARY KEY, grade TEXT, last_close REAL,
            med_turnover_cr REAL, ann_vol_pct REAL, atr_pct REAL, traded_days_1y INTEGER,
            market_cap_cr REAL, quality_score REAL, roe REAL, debt_equity REAL,
            loss_making INTEGER, built_at TEXT DEFAULT (datetime('now'))
        )""")
    cols = ("symbol", "grade", "last_close", "med_turnover_cr", "ann_vol_pct", "atr_pct",
            "traded_days_1y", "market_cap_cr", "quality_score", "roe", "debt_equity", "loss_making")
    conn.executemany(
        f"INSERT INTO tradeable_universe ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
        [tuple(r[c] for c in cols) for r in rows])
    conn.commit()

    # human-readable CSV, best-first: A/B/C/illiquid, then lowest vol, then turnover
    order = {"A core": 0, "B tradeable": 1, "C volatile": 2, "illiquid": 3, "etf": 4}
    rows.sort(key=lambda r: (order[r["grade"]], r["ann_vol_pct"] or 1e9, -(r["med_turnover_cr"] or 0)))
    with open(CSV_OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows([{c: r[c] for c in cols} for r in rows])

    from collections import Counter
    dist = Counter(r["grade"] for r in rows)
    print(f"tradeable_universe: {len(rows)} symbols → DB table + {CSV_OUT}")
    for g in ("A core", "B tradeable", "C volatile", "illiquid", "etf"):
        print(f"  {g:<12} {dist.get(g, 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
