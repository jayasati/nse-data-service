"""Engine 12 — Management Credibility. "Did management actually deliver?" Distinct
from Quality (which scores the LEVEL/rate of growth): Credibility scores the
RELIABILITY of delivery and whether loud claims convert to results.

Two parts, point-in-time:
  1. DELIVERY TRACK RECORD (deep, robust — from extracted_financials): over the last
     up-to-8 reported quarters, the hit-rate of positive YoY revenue & PAT growth,
     docked for ERRATIC delivery (high revenue-growth volatility = boom-bust, less
     reliable than a steady compounder).
  2. CLAIM CONVERSION (recent, best-effort — announcements are only ~2.5mo deep):
     if management made several forward-looking claims (orders/acquisition/expansion/
     product) in the trailing window, did revenue actually grow? Loud-but-flat =
     over-promising penalty; claims + real growth = small conversion bonus.

Output: Credibility Score [0,100]. A high-growth-but-erratic-promotional name scores
LOW even with high Quality; a steady under-promise/over-deliver compounder scores HIGH.
DISPLAY/context only (per spec it's in Final Output, not a Buy-Score input). None when
fewer than 4 YoY-comparable quarters exist (no track record yet).
"""
from __future__ import annotations

import datetime as _dt
import statistics as _st

from . import news_engine

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
WINDOW_DAYS = 180
CLAIM_TYPES = {"order_win", "acquisition", "expansion", "product_launch"}


def _bdt_epoch(s):
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return int(_dt.datetime.strptime(s.strip(), fmt).replace(tzinfo=_IST).timestamp())
        except ValueError:
            continue
    return None


def _pit_quarters(conn, symbol, as_of_ep):
    """{period_ending: (top_line, pat)} consolidated-preferred, reported on/before
    as_of. Top line = revenue, or NII/interest-earned for banks (no 'revenue' line)."""
    by_pe = {}
    for pe, scope, rev, nii, ie, pat, bdt in conn.execute(
            "SELECT period_ending, scope, revenue_cr, net_interest_income_cr, "
            "interest_earned_cr, pat_cr, broadcast_dt FROM extracted_financials WHERE symbol=?",
            (symbol,)):
        ep = _bdt_epoch(bdt)
        if ep is None or ep > as_of_ep or not pe:
            continue
        top = rev if rev is not None else nii if nii is not None else ie
        cur = by_pe.get(pe)
        if cur is None or (scope == "consolidated" and cur[0] != "consolidated"):
            by_pe[pe] = (scope, top, pat)
    return [(pe, by_pe[pe][1], by_pe[pe][2]) for pe in sorted(by_pe)]


def _yoy(quarters):
    """Per quarter, YoY rev/pat growth vs the ~365d-prior period (±45d). Returns the
    last up-to-8 as list of (rev_yoy, pat_yoy) (either may be None)."""
    pes = [q[0] for q in quarters]
    rev = {q[0]: q[1] for q in quarters}
    pat = {q[0]: q[2] for q in quarters}
    out = []
    for pe in pes:
        try:
            tgt = _dt.date.fromisoformat(pe) - _dt.timedelta(days=365)
        except ValueError:
            continue
        yp = min(pes, key=lambda p: abs((_dt.date.fromisoformat(p) - tgt).days), default=None)
        if not yp or abs((_dt.date.fromisoformat(yp) - tgt).days) > 45:
            continue
        def g(now, prior):
            return (now - prior) / abs(prior) * 100 if (now is not None and prior not in (None, 0)) else None
        out.append((g(rev[pe], rev[yp]), g(pat[pe], pat[yp])))
    return out[-8:]


def _claim_count(conn, symbol, as_of_ep):
    """Count of recent forward-looking POSITIVE claims (orders/acq/expansion/product)."""
    lo = as_of_ep - WINDOW_DAYS * 86400
    n = 0
    for subj, det, sent, bep in conn.execute(
            "SELECT subject, details, sentiment, broadcast_epoch FROM raw_announcements "
            "WHERE symbol=? AND broadcast_epoch BETWEEN ? AND ?", (symbol, lo, as_of_ep)):
        if news_engine.classify(subj, det, sent) in CLAIM_TYPES:
            n += 1
    return n


def credibility_raw(conn, symbol, as_of_ep) -> dict | None:
    yoy = _yoy(_pit_quarters(conn, symbol, as_of_ep))
    rev_g = [g for g, _ in yoy if g is not None]
    pat_g = [g for _, g in yoy if g is not None]
    if len(rev_g) < 4:
        return None
    rev_hit = sum(1 for g in rev_g if g > 0) / len(rev_g)
    pat_hit = (sum(1 for g in pat_g if g > 0) / len(pat_g)) if pat_g else rev_hit
    delivery = 100.0 * (0.5 * rev_hit + 0.5 * pat_hit)
    # erratic delivery (boom-bust) is less credible than steady growth
    erratic = 0.0
    if len(rev_g) >= 3:
        erratic = min(20.0, _st.pstdev(rev_g) * 0.30)
    delivery = max(0.0, min(100.0, delivery - erratic))

    # claim conversion (best-effort, recent)
    claims = _claim_count(conn, symbol, as_of_ep)
    latest_rev = rev_g[-1]
    adj = 0.0
    if claims >= 2:
        adj = 6.0 if latest_rev > 10 else -12.0 if latest_rev <= 0 else 0.0
    score = round(max(0.0, min(100.0, delivery + adj)), 1)
    return {"score": score,
            "components": {"delivery": round(delivery, 1), "rev_hit": round(rev_hit, 2),
                           "pat_hit": round(pat_hit, 2), "erratic": round(erratic, 1),
                           "claims": claims, "latest_rev_yoy": round(latest_rev, 1),
                           "conversion_adj": adj},
            "n_quarters": len(rev_g)}


def score_universe(conn, symbols, as_of_ep, sector_of=None) -> dict:
    """{symbol: {'score', 'components', ...}} — absolute credibility (delivery track
    record + claim conversion). Sparse: omits names without ≥4 YoY-comparable quarters."""
    out = {}
    for s in symbols:
        r = credibility_raw(conn, s, as_of_ep)
        if r:
            out[s] = r
    return out
