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


def macro_risk(conn, as_of_ep: int) -> dict:
    """{score, state, vix, components, missing} — higher score = safer / risk-on."""
    vix_sc, vix = _vix_safety(conn, as_of_ep)
    breadth_sc = _breadth_safety(conn, as_of_ep)
    fii_sc = _fii_safety(conn, as_of_ep)
    comps = {"vix": vix_sc, "breadth": breadth_sc, "fii_flow": fii_sc}
    wts = {"vix": 0.5, "breadth": 0.3, "fii_flow": 0.2}
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
            "missing": ["oil/Brent", "USDINR", "rates"]}
