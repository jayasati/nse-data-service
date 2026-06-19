"""Engine 1 — Macro Shock engine (grand-prompt v2). Answers "is the macro tape
overwhelming stock-specific factors?" → a Macro Risk Score [0,100] (higher = safer /
risk-on) and a state {Risk On, Neutral, Risk Off, Panic} that overlays the Buy
Decision (a great stock is still a poor buy in a panic).

Inputs from the free feeds we actually have, point-in-time:
  * India VIX (raw_india_vix) — fear gauge; level + one-day spike
  * Market breadth (market_state.advance_decline_ratio) — participation
  * FII/DII net flows (raw_fii_dii) — institutional risk appetite (best-effort: the
    feed is shallow today, so a missing read is treated neutral, never a false panic)

NOT yet wired (no free feed ingested): oil/Brent, USDINR, rates shock. The score
degrades gracefully to the available inputs and notes what's missing.
"""
from __future__ import annotations

import datetime as _dt

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _bucket(x, table, default=50.0):
    """table: list of (threshold, score) ascending by threshold; first hit wins."""
    if x is None:
        return default
    for thr, sc in table:
        if x <= thr:
            return sc
    return table[-1][1]


def _vix_safety(conn, as_of_ep):
    r = conn.execute("SELECT vix, vix_pct_change FROM raw_india_vix WHERE as_of<=? "
                     "ORDER BY as_of DESC LIMIT 1", (as_of_ep,)).fetchone()
    if not r or r[0] is None:
        return None, None
    vix, chg = r[0], r[1]
    # lower VIX = safer
    sc = _bucket(vix, [(12, 100), (15, 85), (18, 70), (22, 50), (28, 30), (99, 10)])
    if chg is not None and chg >= 15:           # a sharp one-day VIX spike = fresh stress
        sc -= 15
    return max(0.0, sc), vix


def _breadth_safety(conn, as_of_ep):
    iso = _dt.datetime.fromtimestamp(as_of_ep, _IST).isoformat()
    r = conn.execute("SELECT advance_decline_ratio FROM market_state WHERE as_of<=? "
                     "ORDER BY as_of DESC LIMIT 1", (iso,)).fetchone()
    if not r or r[0] is None:
        return None
    # higher advance/decline = broader participation = safer
    return _bucket(r[0], [(0.5, 20), (0.8, 35), (1.2, 55), (2.0, 75), (1e9, 90)])


def _fii_safety(conn, as_of_ep):
    """Net FII flow over the last few sessions on/before as_of (₹cr). Buying = safer."""
    cutoff = _dt.datetime.fromtimestamp(as_of_ep, _IST).date()
    flows = []
    for d, net in conn.execute(
            "SELECT date, net_value FROM raw_fii_dii WHERE category LIKE 'FII%' OR category LIKE 'FPI%'"):
        try:
            parts = d.split("-")
            dd = _dt.date(int(parts[2]), _MONTHS[parts[1]], int(parts[0]))
        except Exception:  # noqa: BLE001
            continue
        if dd <= cutoff and net is not None:
            flows.append((dd, net))
    if not flows:
        return None
    flows.sort()
    recent = sum(n for _, n in flows[-3:])      # last ~3 sessions net
    return _bucket(recent, [(-5000, 20), (-1500, 40), (1500, 55), (5000, 75), (1e9, 90)])


def nifty_regime(conn, as_of_ep: int) -> str:
    """Point-in-time market regime from NIFTYBEES trend (close vs 50/200-DMA) + VIX —
    reconstructable for any historical date (unlike the live market_state snapshot),
    so it's leak-free in a backtest. Returns a REGIME_WEIGHTS key."""
    rows = conn.execute(
        "SELECT close FROM raw_intraday_candles WHERE symbol='NIFTYBEES' AND interval='day' "
        "AND close IS NOT NULL AND ts<=? ORDER BY ts DESC LIMIT 200", (as_of_ep,)).fetchall()
    if len(rows) < 200:
        return "neutral"
    c = [r[0] for r in rows][::-1]
    px, s50, s200 = c[-1], sum(c[-50:]) / 50.0, sum(c) / 200.0
    vr = conn.execute("SELECT vix FROM raw_india_vix WHERE as_of<=? ORDER BY as_of DESC LIMIT 1",
                      (as_of_ep,)).fetchone()
    vix = vr[0] if vr else None
    if vix is not None and vix > 28:
        return "panic"
    if px > s50 > s200:
        return "strong_bull" if (vix is not None and vix < 13) else "bull"
    if px < s200 and px < s50:
        return "bear"
    return "neutral"


def _chg(conn, col, as_of_ep, lookback=10):
    """% change of a raw_macro_market series over ~lookback sessions on/before as_of."""
    iso = _dt.datetime.fromtimestamp(as_of_ep, _IST).date().isoformat()
    try:
        rows = conn.execute(
            f"SELECT {col} FROM raw_macro_market WHERE {col} IS NOT NULL AND date<=? "
            "ORDER BY date DESC LIMIT ?", (iso, lookback + 1)).fetchall()
    except Exception:  # noqa: BLE001 — table optional
        return None
    if len(rows) < lookback + 1 or not rows[-1][0]:
        return None
    return (rows[0][0] - rows[-1][0]) / rows[-1][0] * 100.0


def _currency_safety(conn, as_of_ep):
    chg = _chg(conn, "usdinr", as_of_ep)            # USDINR up = rupee weaker = risk-off
    return None if chg is None else _bucket(chg, [(-2, 85), (0, 70), (1.5, 55), (3, 40), (1e9, 20)])


def _oil_safety(conn, as_of_ep):
    chg = _chg(conn, "brent", as_of_ep)             # Brent up = oil shock = risk-off (India imports)
    return None if chg is None else _bucket(chg, [(-5, 85), (0, 70), (5, 55), (12, 40), (1e9, 25)])


def _latest_le(conn, sql, as_of_ep):
    """Latest scalar from a query parameterised by an ISO date <= as_of. None on
    missing table/empty."""
    iso = _dt.datetime.fromtimestamp(as_of_ep, _IST).date().isoformat()
    try:
        r = conn.execute(sql, (iso,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return r[0] if r and r[0] is not None else None


def _inflation_safety(conn, as_of_ep):
    cpi = _latest_le(conn, "SELECT cpi_yoy FROM raw_macro_market WHERE cpi_yoy IS NOT NULL "
                     "AND date<=? ORDER BY date DESC LIMIT 1", as_of_ep)
    return None if cpi is None else _bucket(cpi, [(4, 85), (5, 70), (6, 55), (7, 40), (1e9, 25)])


def _geopolitical_safety(conn, as_of_ep):           # Caldara-Iacoviello GPR; ~100 = baseline
    gpr = _latest_le(conn, "SELECT gpr FROM raw_macro_market WHERE gpr IS NOT NULL "
                     "AND date<=? ORDER BY date DESC LIMIT 1", as_of_ep)
    return None if gpr is None else _bucket(gpr, [(80, 90), (120, 70), (160, 55), (220, 40), (1e9, 25)])


def _rates_safety(conn, as_of_ep):                  # 10y G-sec yield (manual raw_macro_rates)
    y = _latest_le(conn, "SELECT gsec_10y_yield FROM raw_macro_rates WHERE gsec_10y_yield IS NOT NULL "
                   "AND as_of_date<=? ORDER BY as_of_date DESC LIMIT 1", as_of_ep)
    return None if y is None else _bucket(y, [(6.5, 80), (7, 65), (7.5, 50), (8, 35), (1e9, 20)])


def macro_risk(conn, as_of_ep: int) -> dict:
    """{score, state, vix, components, missing} — higher score = safer / risk-on."""
    vix_sc, vix = _vix_safety(conn, as_of_ep)
    comps = {"vix": vix_sc, "breadth": _breadth_safety(conn, as_of_ep),
             "fii_flow": _fii_safety(conn, as_of_ep),
             "currency": _currency_safety(conn, as_of_ep), "oil": _oil_safety(conn, as_of_ep),
             "inflation": _inflation_safety(conn, as_of_ep),
             "rates": _rates_safety(conn, as_of_ep),
             "geopolitical": _geopolitical_safety(conn, as_of_ep)}
    wts = {"vix": 0.30, "breadth": 0.15, "fii_flow": 0.1, "currency": 0.1, "oil": 0.1,
           "inflation": 0.1, "rates": 0.1, "geopolitical": 0.05}
    num = den = 0.0
    for k, sc in comps.items():
        if sc is not None:
            num += sc * wts[k]
            den += wts[k]
    score = round(num / den, 1) if den else None
    state = ("Risk On" if score is None or score >= 70 else
             "Neutral" if score >= 50 else "Risk Off" if score >= 30 else "Panic")
    return {"score": score, "state": state, "vix": vix,
            "components": {k: (round(v, 1) if v is not None else None) for k, v in comps.items()},
            "missing": [k for k, v in comps.items() if v is None]}
