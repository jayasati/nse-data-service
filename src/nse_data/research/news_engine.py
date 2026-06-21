"""Engine 6 — News Intelligence. Converts raw announcements/news into STRUCTURED
event signals (not simple sentiment): each item is classified to an event TYPE, given
a signed severity, and decayed by a TYPE-SPECIFIC half-life (an order win fades slowly,
a dividend fast, an auditor exit barely fades). Two point-in-time outputs per stock:

  news_score  [0,100]  — positive information flow (orders/acquisitions/expansion/
                         buyback/…); 50 = quiet. The "catalyst of news" momentum.
  news_risk   [0,100]  — higher = safer; bad news (governance/regulatory/penalty/
                         downgrade/pledge) pulls it down. Overlaps risk_engine by
                         design (this is the news-flow view; risk_engine is the gate).

Classification is deterministic keyword rules on the NSE announcement subject + details
(explainable), with sentiment only as a tie-breaker (rating up/down, general news).
Point-in-time via broadcast_epoch ≤ as_of. Severities/half-lives are documented
heuristics; validated by scripts/backtest_engine.py --engine news before any weight.
"""
from __future__ import annotations

import math

WINDOW_DAYS = 180

# event_type -> (sign, base_severity 1-10, decay half-life days)
EVENT_TYPES = {
    "order_win":      (+1, 6, 90),
    "acquisition":    (+1, 7, 120),
    "expansion":      (+1, 5, 120),
    "product_launch": (+1, 4, 90),
    "buyback":        (+1, 6, 60),
    "bonus_split":    (+1, 4, 45),
    "dividend":       (+1, 2, 30),
    "fundraise":      (+1, 3, 60),
    "rating_up":      (+1, 5, 120),
    "positive_news":  (+1, 2, 21),
    "auditor_exit":   (-1, 9, 180),
    "kmp_exit":       (-1, 4, 120),
    "regulatory":     (-1, 7, 150),
    "penalty":        (-1, 6, 120),
    "rating_down":    (-1, 7, 150),
    "pledge":         (-1, 5, 120),
    "negative_news":  (-1, 3, 30),
}


def _sentiment_val(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().lower()
    return {"positive": 1.0, "bullish": 1.0, "negative": -1.0, "bearish": -1.0,
            "neutral": 0.0}.get(t)


def classify(subject: str | None, details: str | None, sentiment=None) -> str | None:
    """NSE announcement → event type (or None = ignore). Ordered: specific first."""
    t = f"{subject or ''} {details or ''}".lower()
    has = lambda *ws: any(w in t for w in ws)
    # routine compliance filings that merely CITE SEBI/regulations are not events —
    # drop them so they don't masquerade as a regulatory action.
    if has("certificate under", "disclosure under", "compliance certificate",
           "intimation under regulation", "submission under regulation",
           "reg. 74", "regulation 74", "regulation 7(3)", "regulation 40"):
        return None
    # --- negatives (checked first; they dominate the same filing) ---
    # auditor RESIGNATION/removal is the red flag; routine "change in / appointment of auditor"
    # (mandatory 5-yr rotation, ratification) is NOT — don't tag those severity-9 negatives.
    if has("auditor") and has("resignation", "resign", "cessation", "removal") \
            and not has("appoint", "ratif", "re-appoint"):
        return "auditor_exit"
    # pledge/encumbrance, BUT not the NEGATED forms ("no new (share) encumbrances", "confirms no
    # pledge", "pledge released/revoked", "reduction in pledge") — those are neutral/positive.
    _pledge_negated = has("no new", "declares no", "confirms no", "nil encumbr", "no encumbr",
                          "no pledge", "without encumbr", "free of encumbr", "release of pledge",
                          "pledge released", "revocation", "reduction in pledge", "no shares pledged")
    if has("pledge", "invocation", "encumbrance") and not _pledge_negated:
        return "pledge"
    # insolvency/NCLT is a negative — UNLESS it's an NCLT-APPROVED merger/scheme (a positive
    # outcome), which the acquisition rule below should claim instead.
    if has("insolvency", "cirp", "default in payment", "winding up") or (
            has("nclt") and not has("approv", "sanction", "dispensation", "convene",
                                    "scheme", "merger", "amalgamation", "arrangement")):
        return "regulatory"
    # adverse regulatory ACTION (not a routine SEBI-regulations citation)
    if has("show cause", "show-cause", "adjudication", "investigation", "search and seiz",
           "summons", "prosecution", "sebi order", "interim order", "order passed against",
           "debarment", "impound", "freezing of", "settlement order", "regulatory action"):
        return "regulatory"
    if has("penalty", "penalt", "fine of", "demand notice", "tax demand"):
        return "penalty"
    if has("resignation", "cessation", "resign") and has("director", "kmp", "cfo", "ceo",
            "managing director", "company secretary", "whole-time", "chairman"):
        return "kmp_exit"
    # --- positives ---
    if has("buyback", "buy-back", "buy back"):
        return "buyback"
    if has("bonus", "stock split", "sub-division", "subdivision"):
        return "bonus_split"
    if has("bagging", "receiving of orders", "work order", "letter of award", "letter of intent",
           "purchase order", "new order", "order win", "secures order", "wins order", "contract"):
        return "order_win"
    if has("acquisition", "acquire", "amalgamation", "merger", "scheme of arrangement"):
        return "acquisition"
    if has("capacity", "expansion", "new plant", "greenfield", "brownfield", "commission",
           "commercial production", "capex", "new facility"):
        return "expansion"
    if has("launch", "new product", "unveil"):
        return "product_launch"
    if has("qip", "preferential issue", "fund rais", "raising of funds", "rights issue", "warrant"):
        return "fundraise"
    # NB: bare "rating" matches "opeRATING" — require specific credit-rating terms.
    if has("credit rating", "rating action", "rating agency", "rating assigned", "rating revised",
           "rating upgrade", "rating downgrade", "rating reaffirm", "reaffirmation of rating",
           "icra", "crisil", "care ratings", "ind-ra", "india ratings", "rating of"):
        sv = _sentiment_val(sentiment)
        return "rating_down" if (sv is not None and sv < 0) else "rating_up"
    if has("dividend"):
        return "dividend"
    # --- fall back to sentiment for general news/press releases ---
    sv = _sentiment_val(sentiment)
    if sv is not None and (has("press release", "general update", "update", "news", "media")):
        if sv > 0.2:
            return "positive_news"
        if sv < -0.2:
            return "negative_news"
    return None


def news_raw(conn, symbol: str, as_of_ep: int) -> dict:
    """{news_score, news_risk, events, top_pos, top_neg} from announcements (+ news if
    present) in the trailing WINDOW on/before as_of. Always returns a dict (50/100 quiet)."""
    lo = as_of_ep - WINDOW_DAYS * 86400
    events = []
    for subj, det, sent, bep in conn.execute(
            "SELECT subject, details, sentiment, broadcast_epoch FROM raw_announcements "
            "WHERE symbol=? AND broadcast_epoch IS NOT NULL AND broadcast_epoch BETWEEN ? AND ?",
            (symbol, lo, as_of_ep)):
        et = classify(subj, det, sent)
        if et:
            events.append((et, bep, subj))
    # optional raw_news corpus (headlines) — sentiment-classified general news
    try:
        for head, sent, pep in conn.execute(
                "SELECT headline, NULL, published_epoch FROM raw_news "
                "WHERE symbol=? AND published_epoch BETWEEN ? AND ?", (symbol, lo, as_of_ep)):
            et = classify(head, None, sent)
            if et:
                events.append((et, pep, head))
    except Exception:  # noqa: BLE001 — raw_news optional
        pass

    pos = neg = 0.0
    top_pos = top_neg = None
    for et, bep, subj in events:
        sign, sev, hl = EVENT_TYPES[et]
        age = max(0.0, (as_of_ep - bep) / 86400.0)
        contrib = sev * (0.5 ** (age / hl))
        if sign > 0:
            pos += contrib
            if top_pos is None or contrib > top_pos[1]:
                top_pos = (et, contrib, subj)
        else:
            neg += contrib
            if top_neg is None or contrib > top_neg[1]:
                top_neg = (et, contrib, subj)
    news_score = round(50 + 50 * math.tanh(pos / 8.0), 1)
    news_risk = round(100 - 50 * math.tanh(neg / 8.0), 1)
    return {"news_score": news_score, "news_risk": news_risk, "n_events": len(events),
            "top_pos": top_pos, "top_neg": top_neg}


def score_universe(conn, symbols, as_of_ep, sector_of=None) -> dict:
    """{symbol: {'score','news_risk',...}} — news_score is the rankable positive-flow
    signal (absolute, 50=quiet). Not sector-relative (news is idiosyncratic)."""
    out = {}
    for s in symbols:
        r = news_raw(conn, s, as_of_ep)
        out[s] = {"score": r["news_score"], "news_risk": r["news_risk"],
                  "top_pos": r["top_pos"], "top_neg": r["top_neg"]}
    return out


# ---- nightly persistence (Phase-2 news-impact scores → news_daily) ----------
def run_news_score_pass(conn, *, symbols=None, as_of_ep=None) -> dict:
    """Score the tracked universe's news flow and persist to news_daily (point-in-time). Quiet
    names (no classified events in the window) are skipped to keep the table actionable."""
    import datetime as _dt
    import time as _t

    from ..universe import tracked_symbols
    as_of_ep = as_of_ep or int(_t.time())
    symbols = sorted(tracked_symbols()) if symbols is None else symbols
    today = _dt.date.fromtimestamp(as_of_ep).isoformat()
    now = int(_t.time())
    drv = lambda x: f"{x[0]}: {str(x[2])[:60]}" if x else None
    written = 0
    for s in symbols:
        try:
            r = news_raw(conn, s, as_of_ep)
        except Exception:  # noqa: BLE001
            continue
        if not r.get("n_events"):
            continue
        conn.execute(
            "INSERT OR REPLACE INTO news_daily (as_of_date, symbol, news_score, news_risk, "
            "n_events, top_pos, top_neg, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (today, s, r["news_score"], r["news_risk"], r["n_events"],
             drv(r["top_pos"]), drv(r["top_neg"]), now))
        written += 1
    conn.commit()
    return {"date": today, "scored": written}


def register_news_score_job(scheduler, db_path: str) -> str:
    """Persist universe news-impact scores nightly at 17:00 IST (after collect_news's 16:30 run)."""
    import structlog
    from apscheduler.triggers.cron import CronTrigger

    from ..scheduler import market_hours
    from ..storage.db import open_db
    log = structlog.get_logger()

    def _tick():
        conn = open_db(db_path)
        try:
            log.info("news_score", **run_news_score_pass(conn))
        except Exception:
            log.exception("news_score_failed")
        finally:
            conn.close()

    scheduler.add_job(_tick, trigger=CronTrigger(hour=17, minute=0, timezone=market_hours.IST),
                      id="news_score", max_instances=1, coalesce=True, replace_existing=True)
    return "news_score"
