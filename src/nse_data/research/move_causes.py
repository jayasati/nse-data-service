"""Attribute a single-day stock move to its likely CAUSE, via external news +
LLM — the "why" layer for intraday_move_events (plans/INTRADAY_MOVE_RESEARCH_PLAN.md).

Pipeline per move event (symbol, date, direction, move%):
  1. SEARCH — Google News RSS with DATE OPERATORS (after:/before:) for a window
     ENDING at the move date, then keep only items whose pubDate is ≤ the move
     date. This is the crucial bit: the cause must be news published AT OR
     BEFORE the move — never later coverage. Closest-to-move first.
  2. ATTRIBUTE — LLM reads the dated headlines + move context and returns a
     structured cause: {category, summary, source_url, confidence, idiosyncratic}.
  3. STORE — into `move_causes`, keyed (symbol, date), linked to intraday_move_events.

Category taxonomy: company | group | sector | macro | technical | unknown.
``idiosyncratic`` = company-specific vs group/sector/macro spillover.

Cost: one small LLM read per move + one HTTP get. Run only on significant,
tracked-universe moves (the script gates this), cached by (symbol, date).
"""
from __future__ import annotations

import datetime as dt
import json
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import structlog

from ..events.consensus_sources.news import _TIMEOUT
from . import cause_engine

log = structlog.get_logger()

CATEGORIES = ("company", "brokerage", "group", "sector", "macro", "technical", "unknown")

_GNEWS_URL = "https://news.google.com/rss/search"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-IN,en;q=0.9",
}

_ATTRIBUTION_SCHEMA = {
    "category": "one of: company, group, sector, macro, technical, unknown",
    "cause_summary": "<=180 chars, the specific reason (cite the fact, not 'news')",
    "source_title": "the headline that best evidences it, copied verbatim, or null",
    "confidence": "0.0-1.0 — how well the dated news explains THIS day's move",
    "idiosyncratic": "true if company-specific; false if group/sector/macro spillover",
}


def ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS move_causes (
            symbol TEXT, date TEXT, direction TEXT, move_pct REAL,
            category TEXT, cause_summary TEXT, source_url TEXT, source_date TEXT,
            confidence REAL, idiosyncratic INTEGER, regime TEXT,
            n_articles INTEGER, searched_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (symbol, date)
        )""")
    # additive migrations for DBs created before these columns existed
    for col in ("regime TEXT", "cause_label TEXT"):
        try:
            conn.execute(f"ALTER TABLE move_causes ADD COLUMN {col}")
        except Exception:  # noqa: BLE001 — column already present
            pass


def _company_name(conn, symbol: str) -> str:
    for sql in ("SELECT company_name FROM raw_quote_metadata WHERE symbol=?",
                "SELECT company_name FROM raw_financial_results WHERE symbol=? LIMIT 1"):
        try:
            row = conn.execute(sql, (symbol,)).fetchone()
            if row and row[0]:
                return str(row[0])
        except Exception:  # noqa: BLE001 — table may not exist
            pass
    return symbol


def _parse_date(s: str):
    try:
        return parsedate_to_datetime(s).date()
    except (TypeError, ValueError):
        return None


def parse_gnews_rss(xml_text: str) -> list[dict]:
    """Google News RSS XML → [{title, url, date}] (date may be None)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        out.append({
            "title": (it.findtext("title") or "").strip(),
            "url": (it.findtext("link") or "").strip(),
            "date": _parse_date(it.findtext("pubDate") or ""),
        })
    return out


def search_news(client, company: str, move_date: str, *, terms: str = "share price",
                window_days: int = 7, limit: int = 10) -> list[dict]:
    """Date-scoped news AT OR BEFORE the move. Query window ends at the move
    date; results are then hard-filtered to pubDate ≤ move_date (so later
    coverage can never be picked up) and sorted closest-to-move first. ``terms``
    scopes the query (default 'share price'; 'SEBI' for the regulatory pass)."""
    md = dt.date.fromisoformat(move_date)
    after = (md - dt.timedelta(days=window_days)).isoformat()
    before = (md + dt.timedelta(days=1)).isoformat()   # before: is exclusive → +1d includes the move day
    query = f"{company} {terms} after:{after} before:{before}"
    r = client.get(_GNEWS_URL, params={"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
    r.raise_for_status()
    arts = [a for a in parse_gnews_rss(r.text) if a["date"] and a["date"] <= md]
    arts.sort(key=lambda a: a["date"], reverse=True)   # closest to the move first
    return arts[:limit]


def _fmt_date(date: str) -> str:
    try:
        return dt.date.fromisoformat(date).strftime("%d %B %Y")
    except ValueError:
        return date


def attribute(llm, *, symbol, company, date, direction, move_pct,
              candidates: list[dict]) -> dict | None:
    """LLM reads ALL dated candidates (internal events + news) → structured cause."""
    move_dir = "ROSE" if direction == "up" else "FELL"
    heads = "\n".join(
        f"- [{c.get('event_date')}] ({c.get('source')}) {c.get('summary')}" for c in candidates
    ) or "(no pre-move catalysts found)"
    prompt = (
        f"A stock moved sharply intraday and we want the CAUSE — which must be a "
        f"catalyst dated ON OR BEFORE the move date, never later coverage.\n\n"
        f"Stock: {company} ({symbol}, NSE India)\n"
        f"Date: {_fmt_date(date)}\n"
        f"Move: {move_dir} {abs(move_pct):.1f}% during the session (measured from the day's open).\n\n"
        f"Candidate catalysts from internal NSE feeds + news, dated on/before the move "
        f"(closest first):\n{heads}\n\n"
        f"Pick the catalyst dated at or just before the move. Categories:\n"
        f"- company: results, capex/commissioning, fundraise/QIP/bonds, credit rating, promoter/stake\n"
        f"- brokerage: an equity broker/analyst (Jefferies, JPMorgan, Morgan Stanley, CLSA, Motilal, etc.) "
        f"initiates/upgrades/downgrades or changes target price / buy-sell call\n"
        f"- group: parent/group-wide news, peer contagion, short-seller report, SEC/SEBI/DOJ action\n"
        f"- sector: policy, sector peers, interest rates, input costs\n"
        f"- macro: index move, FII flows, MSCI/index rebalance, currency\n"
        f"- technical: circuit limit, F&O OI/short-covering/expiry, block/bulk deals\n"
        f"- unknown: no dated catalyst plausibly explains the move\n\n"
        f"RULES:\n"
        f"1. If a 'market'/'systematic' candidate shows the move largely tracked the "
        f"index/sector, the cause is SYSTEMATIC → category 'macro'; do NOT invent a "
        f"stock-specific reason.\n"
        f"2. Magnitude check: the catalyst must plausibly justify a {abs(move_pct):.1f}% move. "
        f"A minor item (small dividend, routine filing) does NOT explain a large move — "
        f"lower confidence or use 'unknown'.\n"
        f"3. An 'ex_date_artifact' candidate means the price adjusted mechanically — say so.\n\n"
        f"Return ONLY JSON with keys: {json.dumps(_ATTRIBUTION_SCHEMA)}"
    )
    res = llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}, max_tokens=400, temperature=0.0,
    )
    if not res.success or not res.parsed_json:
        log.info("attribute_failed", symbol=symbol, date=date, error=res.error)
        return None
    j = res.parsed_json
    cat = str(j.get("category", "unknown")).lower().strip()
    if cat not in CATEGORIES:
        cat = "unknown"
    try:
        conf = round(float(j.get("confidence")), 2)
    except (TypeError, ValueError):
        conf = None
    # map the chosen candidate back to its url + date (the LLM cites by summary)
    src_title = (j.get("source_title") or "").strip().lower()
    src = next((c for c in candidates if src_title and src_title in (c.get("summary") or "").lower()), None)
    src = src or (candidates[0] if candidates else None)
    return {
        "category": cat,
        "cause_summary": (j.get("cause_summary") or "")[:300],
        "source_url": src.get("url") if src else None,
        "source_date": src.get("event_date") if src else None,
        "confidence": conf,
        "idiosyncratic": 1 if j.get("idiosyncratic") in (True, "true", 1) else 0,
        "cost_usd": res.cost_usd,
    }


def find_cause(conn, llm, client, *, symbol: str, date: str, direction: str,
               move_pct: float, window_days: int = 4) -> dict | None:
    """Full multi-source pipeline for one move: gather internal candidates (free,
    cause_engine) + external news (date-scoped), store the full candidate audit
    trail, then LLM-fuse them into one primary cause (also stored)."""
    company = _company_name(conn, symbol)
    # Tier 1 — internal structured catalysts (free)
    candidates = cause_engine.gather_candidates(conn, symbol, date, window_days=window_days)
    # Tier 0 — market/sector beta: is the move idiosyncratic or systematic?
    mkt_cand, regime = cause_engine.market_context(conn, symbol, date, move_pct)
    if mkt_cand:
        candidates.append(mkt_cand)
    # Task 2 — if the symbol's macro-theme basket rotated this day, hand the LLM that context
    # so a correlated move is attributed to the theme, not 'unknown'.
    try:
        from .basket_rotation import active_basket_for
        b = active_basket_for(conn, symbol, date)
        if b:
            candidates.append({
                "source": "basket", "cause_type": "sector", "event_date": date,
                "summary": f"{b['basket']} basket {b['signal_type']} — driver {b['driver']} "
                           f"{b['driver_move_pct']:+.1f}%, breadth-confidence {b['confidence']}",
                "url": None, "weight": 7})
    except Exception:  # noqa: BLE001
        pass
    # Tier 2 — external, date-scoped (≤ move date). Two scoped news passes:
    #   general ("share price") + regulatory ("SEBI"), since SEBI's own site is
    #   not symbol-indexed. NSE direct API is Akamai-blocked → its data comes via
    #   the internal raw_announcements connector above. BSE is best-effort (below).
    for src, terms, wt in (("news", "share price", 5), ("sebi", "SEBI", 8)):
        try:
            for a in search_news(client, company, date, terms=terms, window_days=window_days,
                                 limit=10 if src == "news" else 5):
                candidates.append({
                    "source": src, "cause_type": "regulatory" if src == "sebi" else "news",
                    "event_date": a["date"].isoformat() if a["date"] else None,
                    "summary": a["title"], "url": a["url"], "weight": wt,
                })
        except Exception as e:  # noqa: BLE001 — transient network
            log.info("search_failed", source=src, symbol=symbol, date=date, error=str(e))
    # Tier 2 (best-effort) — BSE announcements (reachable API; needs scrip master)
    try:
        candidates.extend(cause_engine.bse_announcements(client, conn, symbol, date,
                                                          window_days=window_days))
    except Exception as e:  # noqa: BLE001
        log.info("bse_failed", symbol=symbol, date=date, error=str(e))
    # newest first so the LLM sees the closest catalyst at the top
    candidates.sort(key=lambda c: (c.get("event_date") or "", c.get("weight", 0)), reverse=True)
    cause_engine.store_candidates(conn, symbol, date, candidates)   # full audit trail

    cause = attribute(llm, symbol=symbol, company=company, date=date,
                      direction=direction, move_pct=move_pct, candidates=candidates)
    if cause is None:
        return None
    cause["n_articles"] = len(candidates)
    cause["regime"] = regime
    # Task 5C — make "searched but empty" auditable and distinct from "couldn't explain".
    n_news = sum(1 for c in candidates if c.get("cause_type") in ("news", "regulatory")
                 or c.get("source") in ("news", "sebi", "bse"))
    if cause["category"] != "unknown":
        cause["cause_label"] = cause["category"]
    elif n_news == 0:
        cause["cause_label"] = "no_news_found"   # attribution ran, news search returned nothing
    else:
        cause["cause_label"] = "unknown"         # news existed, none explained the move
    store(conn, symbol=symbol, date=date, direction=direction, move_pct=move_pct, cause=cause)
    return cause


def store(conn, *, symbol, date, direction, move_pct, cause: dict) -> None:
    ensure_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO move_causes "
        "(symbol, date, direction, move_pct, category, cause_summary, source_url, "
        " source_date, confidence, idiosyncratic, regime, n_articles, cause_label) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (symbol, date, direction, move_pct, cause["category"], cause["cause_summary"],
         cause["source_url"], cause.get("source_date"), cause["confidence"],
         cause["idiosyncratic"], cause.get("regime"), cause.get("n_articles"),
         cause.get("cause_label")))
    conn.commit()


def make_client():
    import httpx
    return httpx.Client(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
