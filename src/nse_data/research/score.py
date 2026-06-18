"""Per-stock conviction score in [0,1] — the aggregation layer over everything we
know about a stock. Transparent + point-in-time: each component contributes an
EXPLICIT, recency-decayed delta to a 0.5 neutral base; the breakdown is returned
so you can see exactly what moved the number. Higher = more constructive setup.

This is a SUMMARY for the human first. It is NOT a validated trading signal until
scripts/validate_score.py shows score-rank actually predicts forward returns
(see plans/STOCK_SCORE_PLAN.md). Weights are heuristic + tunable — the dict below
is the single place to adjust them.
"""
from __future__ import annotations

import datetime as _dt

# Explicit, tunable weights — max signed delta each component adds to the 0.5 base.
WEIGHTS = {
    "momentum": 0.12,      # 20d trend vs its own recent range
    "drawdown": 0.06,      # deep fall from the 60d high (risk-off)
    "order_win": 0.10,     # recent 'orders/contracts bagged' filing(s)
    "analyst": 0.12,       # recent broker upgrade(+) / downgrade(-)
    "news": 0.12,          # net sentiment of recent headlines
    "regulatory": 0.15,    # recent SEBI / regulatory action (penalty)
}
DECAY_DAYS = 25            # a component contribution fades to 0 over ~25 calendar days
NEWS_LOOKBACK_D = 12
EVENT_LOOKBACK_D = 30

_POS = {"surge", "surges", "jump", "jumps", "rally", "rallies", "soar", "soars", "wins",
        "win", "bags", "bag", "order", "orders", "profit", "beat", "beats", "upgrade",
        "upgrades", "record", "high", "strong", "gains", "gain", "approval", "approved",
        "expansion", "expand", "outperform", "buy", "multibagger", "zoom", "zooms"}
_NEG = {"falls", "fall", "crash", "crashes", "drop", "drops", "slump", "slumps", "miss",
        "misses", "downgrade", "downgrades", "loss", "losses", "probe", "fraud", "halt",
        "halts", "ban", "banned", "decline", "declines", "weak", "cut", "cuts", "concern",
        "concerns", "plunge", "plunges", "sell", "warning", "lawsuit", "default"}
# ADVERSE regulatory actions only — NOT benign SEBI disclosures (e.g. a takeover-
# regulation stake disclosure is often a buyer accumulating, i.e. bullish). Bare
# 'sebi' is too broad, so require an adverse term.
_REG_HINTS = ("penalt", "show cause", "investigat", "non-compliance", "freezing of",
              "order passed against", "action taken against", "fraud", "fine imposed")


def _decay(age_days: float) -> float:
    return max(0.0, 1.0 - age_days / DECAY_DAYS)


def _daily_closes(conn, symbol, as_of_ep, n=70):
    rows = conn.execute(
        "SELECT close FROM raw_intraday_candles WHERE symbol=? AND interval='day' "
        "AND close IS NOT NULL AND ts <= ? ORDER BY ts DESC LIMIT ?",
        (symbol, as_of_ep, n)).fetchall()
    return [r[0] for r in rows][::-1]   # oldest→newest


def _recent_announcements(conn, symbol, lo_ep, hi_ep):
    return conn.execute(
        "SELECT subject, broadcast_epoch FROM raw_announcements "
        "WHERE symbol=? AND broadcast_epoch BETWEEN ? AND ?",
        (symbol, lo_ep, hi_ep)).fetchall()


def compute_score(conn, symbol: str, as_of_ep: int) -> dict:
    """Return {'score','base','components':{name:delta},'n'} using only data
    with timestamp <= as_of_ep (point-in-time, no look-ahead)."""
    from ..signals.detect import is_order_win_subject
    comp: dict[str, float] = {}
    day = 86400

    # --- momentum + drawdown (daily candles) ---
    closes = _daily_closes(conn, symbol, as_of_ep)
    if len(closes) >= 21:
        ret20 = closes[-1] / closes[-21] - 1
        comp["momentum"] = WEIGHTS["momentum"] * max(-1.0, min(1.0, ret20 / 0.15))
        hi60 = max(closes[-60:]) if len(closes) >= 60 else max(closes)
        dd = closes[-1] / hi60 - 1
        if dd <= -0.10:
            comp["drawdown"] = -WEIGHTS["drawdown"] * min(1.0, abs(dd) / 0.25)

    # --- order wins + regulatory (announcements) ---
    anns = _recent_announcements(conn, symbol, as_of_ep - EVENT_LOOKBACK_D * day, as_of_ep)
    ow = reg = 0.0
    for subj, ep in anns:
        age = (as_of_ep - ep) / day
        if is_order_win_subject(subj):
            ow += WEIGHTS["order_win"] * _decay(age)
        s = (subj or "").lower()
        if any(h in s for h in _REG_HINTS):
            reg += WEIGHTS["regulatory"] * _decay(age)
    if ow:
        comp["order_win"] = min(WEIGHTS["order_win"] * 1.5, ow)
    if reg:
        comp["regulatory"] = -min(WEIGHTS["regulatory"] * 1.5, reg)

    # --- analyst ratings (recent upgrade/downgrade) ---
    try:
        rows = conn.execute(
            "SELECT new_call, old_call, tier, published_at FROM raw_analyst_ratings "
            "WHERE symbol=? ORDER BY id DESC LIMIT 5", (symbol,)).fetchall()
        from ..parsers.analyst_ratings import call_rank
        a = 0.0
        for nc, oc, tier, _pub in rows:
            nr, orr = call_rank(nc), call_rank(oc)
            if nr is None or orr is None or nr == orr:
                continue
            w = WEIGHTS["analyst"] * (1.0 if tier == 1 else 0.6)
            a += w if nr > orr else -w
        if a:
            comp["analyst"] = max(-WEIGHTS["analyst"], min(WEIGHTS["analyst"], a))
    except Exception:  # noqa: BLE001 — table may be absent
        pass

    # --- news sentiment (recent headlines, lexicon) ---
    try:
        rows = conn.execute(
            "SELECT headline FROM raw_news WHERE symbol=? AND published_epoch >= ? "
            "AND published_epoch <= ?",
            (symbol, as_of_ep - NEWS_LOOKBACK_D * day, as_of_ep)).fetchall()
        pos = neg = 0
        for (h,) in rows:
            toks = set((h or "").lower().replace(",", " ").replace(".", " ").split())
            pos += len(toks & _POS)
            neg += len(toks & _NEG)
        if pos + neg >= 2:
            comp["news"] = WEIGHTS["news"] * (pos - neg) / (pos + neg)
    except Exception:  # noqa: BLE001
        pass

    base = 0.5
    score = base + sum(comp.values())
    return {
        "score": round(max(0.0, min(1.0, score)), 4),
        "base": base,
        "components": {k: round(v, 4) for k, v in comp.items()},
        "n": len(comp),
    }


def as_of_now_epoch() -> int:
    return int(_dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30))).timestamp())
