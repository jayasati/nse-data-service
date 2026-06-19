"""P-Risk engine (grand-prompt). Risk Score [0,100], HIGHER = SAFER. Point-in-time.

Unlike the other engines (cross-sectional percentile), risk is ABSOLUTE adversity:
a clean stock scores ~100, and adverse events SUBTRACT — severity-weighted and
recency-decayed (a 6-month-old red flag matters less than a fresh one). This
matches the spec's absolute event list and is more honest than ranking a
zero-inflated event distribution.

Signals from free data, all gated on disclosure date ≤ as_of:
  - governance : auditor / KMP / director resignations (raw_announcements subjects)
  - regulatory : SEBI/other 'orders passed against', litigation, insolvency (CIRP)
  - credit     : rating downgrade / junk-downgrade / watch-negative (raw_rating_actions)
  - ownership  : promoter STAKE REDUCTION (raw_shareholding_quarterly Δpromoter < 0)
  - financial  : receivables explosion (recv outpacing sales), negative TTM operating
                 cash flow, debt spike (borrowings up >40% YoY) — from the balance-sheet
                 columns (migration 081; debt needs the BS backfill, present on server)
NOT yet available (noted, TODO): promoter PLEDGE (SharesPledged is in the SHP XBRL but
unparsed). Validated as a composite component (does it cut drawdowns?) before weight.
"""
from __future__ import annotations

import datetime as _dt

from .ownership_engine import ownership_raw, _disclosed_ep

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
WINDOW_DAYS = 180          # events older than this are ignored (and decayed within)
_HALFLIFE = 90.0           # adversity halves every 90d

# Adverse subject → points, by category. ONLY unambiguous red flags: the
# subject alone can't tell an adverse 'order passed AGAINST us' / material
# litigation from a routine disclosure (big caps file dozens), so those need
# text-NLP severity (TODO) and are EXCLUDED from v1 to avoid false positives.
_GOV = {
    "Resignation of Statutory Auditor": 35,            # mid-term auditor exit = red flag
    "Resignation of Director/KMP/SMP": 12,             # KMP/board exit
}
_REG = {
    "Corporate Insolvency Resolution Process": 60,     # existential
}
_SUBJ = {**{k: ("governance", v) for k, v in _GOV.items()},
         **{k: ("regulatory", v) for k, v in _REG.items()}}


def _dt_epoch(s: str | None) -> int | None:
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return int(_dt.datetime.strptime(s.strip(), fmt).replace(tzinfo=_IST).timestamp())
        except ValueError:
            continue
    return None


def _financial_adversity(conn, symbol: str, as_of_ep: int) -> float:
    """Balance-sheet red flags (PIT): receivables outpacing sales, negative operating
    cash flow, a debt spike. Returns adversity points (0 = clean). Each guarded on its
    own data — degrades gracefully when a column is absent."""
    by_pe = {}
    for pe, scope, rev, recv, cfo, borr, bdt in conn.execute(
            "SELECT period_ending, scope, revenue_cr, trade_receivables_cr, cfo_cr, "
            "borrowings_cr, broadcast_dt FROM extracted_financials WHERE symbol=?", (symbol,)):
        ep = _dt_epoch(bdt)
        if ep is None or ep > as_of_ep or not pe:
            continue
        cur = by_pe.get(pe)
        if cur is None or (scope == "consolidated" and cur["scope"] != "consolidated"):
            by_pe[pe] = {"scope": scope, "rev": rev, "recv": recv, "cfo": cfo, "borr": borr}
    if len(by_pe) < 2:
        return 0.0
    pes = sorted(by_pe)
    L = by_pe[pes[-1]]
    # year-ago quarter (~365d back, ±45d)
    try:
        tgt = _dt.date.fromisoformat(pes[-1]) - _dt.timedelta(days=365)
        yp = min(pes, key=lambda p: abs((_dt.date.fromisoformat(p) - tgt).days))
        Y = by_pe[yp] if abs((_dt.date.fromisoformat(yp) - tgt).days) <= 45 else None
    except ValueError:
        Y = None
    pts = 0.0

    def g(now, prior):
        return (now - prior) / abs(prior) * 100 if (now is not None and prior not in (None, 0)) else None

    if Y:
        recv_g, rev_g = g(L["recv"], Y["recv"]), g(L["rev"], Y["rev"])
        if recv_g is not None and rev_g is not None and recv_g - rev_g > 25:
            pts += min(20.0, (recv_g - rev_g - 25) / 3.0)        # receivables explosion
        borr_g = g(L["borr"], Y["borr"])
        if borr_g is not None and borr_g > 40 and (L["borr"] or 0) > 50:
            pts += min(15.0, (borr_g - 40) / 4.0)                # debt spike
    # TTM operating cash flow negative = a cash red flag
    cfos = [by_pe[p]["cfo"] for p in pes[-4:] if by_pe[p]["cfo"] is not None]
    if len(cfos) >= 3 and sum(cfos) < 0:
        pts += 18.0
    return pts


def _rating_adversity(action: str | None, is_junk) -> float:
    if is_junk:
        return 40.0
    a = (action or "").lower()
    if "downgrade" in a:
        return 25.0
    if "negative" in a or "watch" in a:
        return 12.0
    return 0.0


def risk_raw(conn, symbol: str, as_of_ep: int) -> dict:
    """{'score','components'} — Risk [0,100], higher = safer. Clean stock → ~100."""
    lo = as_of_ep - WINDOW_DAYS * 86400
    comps = {"governance": 0.0, "regulatory": 0.0, "credit": 0.0, "promoter": 0.0,
             "financial": 0.0}

    def decay(ep):
        return 0.5 ** (((as_of_ep - ep) / 86400) / _HALFLIFE)

    # governance + regulatory announcements (fast via ix_ann_symbol_epoch).
    # MAX per category (not sum) — the WORST single event, so routine repeat
    # filings can't accumulate into a false high-risk reading.
    for subj, bep in conn.execute(
            "SELECT subject, broadcast_epoch FROM raw_announcements "
            "WHERE symbol=? AND broadcast_epoch BETWEEN ? AND ?", (symbol, lo, as_of_ep)):
        hit = _SUBJ.get((subj or "").strip())
        if hit and bep:
            cat, pts = hit
            comps[cat] = max(comps[cat], pts * decay(bep))

    # credit-rating downgrades / watch-negative — worst single action in window
    for action, junk, bdt in conn.execute(
            "SELECT action, is_junk_downgrade, broadcast_dt FROM raw_rating_actions "
            "WHERE symbol=?", (symbol,)):
        ep = _dt_epoch(bdt)
        if ep is None or not (lo <= ep <= as_of_ep):
            continue
        comps["credit"] = max(comps["credit"], _rating_adversity(action, junk) * decay(ep))

    # promoter stake REDUCTION (latest disclosed QoQ Δ ≤ −1%; smaller is noise —
    # ESOP/minor dilution, not a red flag)
    own = ownership_raw(conn, symbol, as_of_ep)
    if own and own.get("d_promoter") is not None and own["d_promoter"] <= -1.0:
        comps["promoter"] = min(40.0, abs(own["d_promoter"]) * 4.0)

    # promoter PLEDGE — latest disclosed ≤ as_of (PIT on the SHP filename timestamp).
    # >15% of promoter holding pledged is a red flag; scales to a 40-pt cap (~70%+).
    try:
        best = None
        for qe, url, pl in conn.execute(
                "SELECT qe_date, xbrl_url, promoter_pledge_pct FROM raw_shareholding_quarterly "
                "WHERE symbol=? AND promoter_pledge_pct IS NOT NULL", (symbol,)):
            ep = _disclosed_ep(url, qe)
            if ep is not None and ep <= as_of_ep and (best is None or ep > best[0]):
                best = (ep, pl)
        if best and best[1] >= 15:
            comps["promoter"] = max(comps["promoter"], min(40.0, (best[1] - 15) * 0.7))
    except Exception:  # noqa: BLE001 — column may be absent on an un-migrated DB
        pass

    # financial red flags (receivables explosion / negative CFO / debt spike)
    comps["financial"] = _financial_adversity(conn, symbol, as_of_ep)

    adversity = sum(comps.values())
    return {"score": round(100.0 - min(100.0, adversity), 1),
            "components": {k: round(v, 1) for k, v in comps.items() if v}}


def score_universe(conn, symbols, as_of_ep: int, sector_of=None) -> dict:
    """{symbol: {'score','components'}} — absolute risk (not cross-sectional)."""
    return {s: risk_raw(conn, s, as_of_ep) for s in symbols}
