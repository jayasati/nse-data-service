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
NOT yet available (noted, TODO): promoter PLEDGE (SharesPledged is in the SHP XBRL
but unparsed) and FINANCIAL risk (debt-spike / CFO-deterioration / receivables —
need per-quarter balance-sheet we don't extract). Validated as a composite
component (does it cut drawdowns / help in down-markets?) before earning weight.
"""
from __future__ import annotations

import datetime as _dt

from .ownership_engine import ownership_raw

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
    comps = {"governance": 0.0, "regulatory": 0.0, "credit": 0.0, "promoter": 0.0}

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

    adversity = sum(comps.values())
    return {"score": round(100.0 - min(100.0, adversity), 1),
            "components": {k: round(v, 1) for k, v in comps.items() if v}}


def score_universe(conn, symbols, as_of_ep: int, sector_of=None) -> dict:
    """{symbol: {'score','components'}} — absolute risk (not cross-sectional)."""
    return {s: risk_raw(conn, s, as_of_ep) for s in symbols}
