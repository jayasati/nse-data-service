"""Analyst broker-recommendation scraper + tier-1 signals (tasks 18.7 / 18.8).

Source: Trendlyne's research-reports firehose
(``https://trendlyne.com/research-reports/all/``) — a no-auth, robots-allowed
HTML table of every broker note across all stocks, newest first. Each row
carries one recommendation: date, stock (NSE symbol in the link slug), broker,
target price and call type (Buy/Accumulate/Hold/Sell…).

(History: the original Moneycontrol RSS feed went permanently Akamai-WAF-blocked
— 503 — around 2026-06; Moneycontrol exposes no free broker-reco JSON. Trendlyne
verified as the reliable free replacement 2026-06-16. ``parse_reco_title`` is
kept for the legacy RSS title format / tests.)

Pipeline (mirrors rating_extractor.py): pure parse functions + a DB-glue pass.
Every parsed reco lands in ``raw_analyst_ratings`` (idempotent via the item
fingerprint). The feed carries only the NEW call/target, so old_call /
old_target come from OUR previous row for the same (symbol, brokerage); an
upgrade/downgrade is a call-rank change between the two. When the brokerage
matches Tier-1 in ``config/brokerage_tiers.yaml`` and the call rank moved, an
``analyst_upgrade_tier1`` / ``analyst_downgrade_tier1`` signal row is written
(dispatched=1 — the alert is sent directly here, like the credit alerts) and a
Telegram message goes out.

    run_analyst_ratings_pass(conn, fetcher=..., sender=...)   # injectable I/O
    register_analyst_ratings_job(scheduler, db_path)          # every 30 min
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from email.utils import format_datetime, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

import structlog

from ..scheduler import market_hours

log = structlog.get_logger()

JOB_ID = "analyst_ratings"
_INTERVAL_SECONDS = 1800            # 30-min cadence (task 18.7)
_FEED_URL = "https://trendlyne.com/research-reports/all/"
_FEED_PAGES = 2                     # newest-first; ~1-2 pages covers a trading day
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_FEED_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-IN,en;q=0.9"}
_TIMEOUT = 20.0
_PAGE_SLEEP = 1.0                   # polite: robots Crawl-delay 1 for normal UAs

# Pseudo-tickers Trendlyne mixes into the feed for non-stock notes (sector /
# economy / index strategy) — drop them; they're not NSE symbols.
_NOT_SYMBOLS = frozenset({
    "INTCOMMOD", "INTWORLDECO", "INTMARKET", "INTGLOBAL", "INTECO",
    "NIFTY", "BANKNIFTY", "INTWORLD", "INTSTRATEGY",
})
_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}
_STOCK_HREF_RE = re.compile(r"/research-reports/stock/\d+/([A-Z0-9&-]+)/")

# Polling window: trading days, 08:30–15:35 IST. Broker notes mostly publish
# pre-open (~08:30–09:15), so a strict market-hours gate would miss the
# morning dump; off-window ticks are cheap no-ops.
_WINDOW_START = (8, 30)
_WINDOW_END = (15, 35)

# Only alert on items published within this window — a cold-start ingest of
# the feed's backlog stays silent.
_ALERT_RECENCY_HOURS = 24

ANALYST_UPGRADE_T1 = "analyst_upgrade_tier1"
ANALYST_DOWNGRADE_T1 = "analyst_downgrade_tier1"

# Call vocabulary → rank. Higher = more bullish; a rank INCREASE vs the
# previous stored call is an upgrade, a decrease a downgrade.
CALL_RANKS = {
    "buy": 2, "accumulate": 2, "add": 2, "outperform": 2, "overweight": 2,
    "hold": 1, "neutral": 1, "equal-weight": 1, "equalweight": 1, "market-perform": 1,
    "sell": 0, "reduce": 0, "underperform": 0, "underweight": 0,
}

_TITLE_RE = re.compile(
    r"^\s*(?P<call>" + "|".join(sorted(CALL_RANKS, key=len, reverse=True)) + r")\s+"
    r"(?P<company>.+?)"
    r"(?:;\s*target\s+of\s+Rs\.?\s*(?P<target>[\d,]+(?:\.\d+)?))?"
    r"\s*:\s*(?P<brokerage>[^:]+?)\s*$",
    re.IGNORECASE,
)

_NAME_NOISE_RE = re.compile(
    r"\b(limited|ltd|india|corporation|corp|company|co|industries|enterprises)\b\.?",
    re.IGNORECASE,
)


# ============================================================================
# Pure parsing
# ============================================================================

def normalize_call(call: str | None) -> str | None:
    c = (call or "").strip().lower()
    return c if c in CALL_RANKS else None


def call_rank(call: str | None) -> int | None:
    return CALL_RANKS.get((call or "").strip().lower())


def parse_reco_title(title: str | None) -> dict | None:
    """'Buy X; target of Rs 3200: Broker' → {call, company, target, brokerage}.

    Returns None for titles that aren't single-stock recommendations
    (sector notes, "Top picks", etc.)."""
    m = _TITLE_RE.match(title or "")
    if not m:
        return None
    target = m.group("target")
    return {
        "call": m.group("call").strip().lower(),
        "company": m.group("company").strip(),
        "target": float(target.replace(",", "")) if target else None,
        "brokerage": m.group("brokerage").strip(),
    }


def parse_rss(xml_text: str) -> list[dict]:
    """RSS XML → [{title, link, published_at}] (lenient; bad XML → [])."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        items.append({
            "title": title,
            "link": (item.findtext("link") or "").strip(),
            "published_at": (item.findtext("pubDate") or "").strip(),
        })
    return items


def _trendlyne_date(text: str) -> str | None:
    """'16 Jun 2026' → RFC822 string at IST midnight (keeps _published_recent /
    storage identical to the old RSS pubDate path). None if unparseable."""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", text or "")
    if not m:
        return None
    day, mon, year = int(m.group(1)), _MONTHS.get(m.group(2).title()), int(m.group(3))
    if not mon:
        return None
    return format_datetime(_dt.datetime(year, mon, day, tzinfo=market_hours.IST))


class _TrendlyneTableParser(HTMLParser):
    """Extract research-report rows from the firehose HTML table. Each <tr> with a
    /research-reports/stock/<id>/<SYMBOL>/ link is one reco; columns are read by
    offset from that stock-link cell (date idx-1, broker +1, target +3, call +6)."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict] = []
        self._in_tr = False
        self._tds: list[str] = []
        self._cur: list[str] | None = None
        self._anchor: int | None = None
        self._slug: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._in_tr, self._tds, self._anchor, self._slug = True, [], None, None
        elif tag == "td" and self._in_tr:
            self._cur = []
        elif tag == "a" and self._in_tr and self._anchor is None:
            href = dict(attrs).get("href") or ""
            m = _STOCK_HREF_RE.search(href)
            if m:
                self._anchor, self._slug = len(self._tds), m.group(1).upper()

    def handle_data(self, data):
        if self._cur is not None and data.strip():
            self._cur.append(data.strip())

    def handle_endtag(self, tag):
        if tag == "td" and self._cur is not None:
            self._tds.append(" ".join(self._cur).strip())
            self._cur = None
        elif tag == "tr" and self._in_tr:
            self._emit()
            self._in_tr = False

    def _emit(self):
        a, tds = self._anchor, self._tds
        if a is None or not self._slug or a + 6 >= len(tds):
            return
        company = tds[a]
        # the broker cell trails responsive labels ("… Reco Target"); keep the name
        broker = re.split(r"\b(?:Reco|Target)\b", tds[a + 1])[0].strip()
        target = tds[a + 3]
        call = tds[a + 6]
        self.rows.append({
            "symbol": self._slug, "company": company, "brokerage": broker,
            "target": target, "call": call, "date": tds[a - 1] if a else "",
        })


def parse_trendlyne(html: str) -> list[dict]:
    """Firehose HTML → [{symbol, company, call, target, brokerage, published_at,
    link, title}]. Drops non-stock pseudo-tickers and calls outside CALL_RANKS."""
    p = _TrendlyneTableParser()
    p.feed(html)
    out = []
    for r in p.rows:
        sym = r["symbol"]
        if sym in _NOT_SYMBOLS:
            continue
        call = normalize_call(r["call"])
        if call is None:                      # e.g. 'Not Rated', 'Under Review'
            continue
        try:
            target = float(str(r["target"]).replace(",", "")) if r["target"] else None
        except ValueError:
            target = None
        published_at = _trendlyne_date(r["date"])
        tgt_s = f"; target of Rs {target:.0f}" if target else ""
        out.append({
            "symbol": sym, "company": r["company"], "call": call,
            "target": target, "brokerage": r["brokerage"],
            "published_at": published_at,
            "link": f"https://trendlyne.com/research-reports/stock//{sym}/",
            "title": f"{call.title()} {r['company']}{tgt_s}: {r['brokerage']}",
        })
    return out


def _normalize_name(name: str) -> str:
    name = _NAME_NOISE_RE.sub(" ", name or "")
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def item_fingerprint(title: str, published_at: str) -> str:
    return hashlib.sha1(f"{title}|{published_at}".encode()).hexdigest()


# ============================================================================
# Brokerage tiers (config/brokerage_tiers.yaml)
# ============================================================================

def load_brokerage_tiers(path: str | Path = "config/brokerage_tiers.yaml") -> dict[str, int]:
    """{normalized brokerage fragment: tier}. Empty when the file is absent."""
    import yaml

    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    out: dict[str, int] = {}
    for tier_name, tier in (("tier1", 1), ("tier2", 2)):
        for name in data.get(tier_name) or []:
            out[_normalize_name(str(name))] = tier
    return out


def match_brokerage_tier(brokerage: str | None, tiers: dict[str, int]) -> int | None:
    """Tier for a feed brokerage string, by normalized substring match."""
    norm = _normalize_name(brokerage or "")
    if not norm:
        return None
    for fragment, tier in tiers.items():
        if fragment and fragment in norm:
            return tier
    return None


# ============================================================================
# Symbol resolution (company display name → NSE symbol)
# ============================================================================

def build_company_symbol_map(conn: sqlite3.Connection) -> dict[str, str]:
    """{normalized company name: symbol} from tables that carry both."""
    out: dict[str, str] = {}
    for query in (
        "SELECT DISTINCT company_name, symbol FROM raw_board_meetings "
        "WHERE company_name IS NOT NULL",
        "SELECT DISTINCT company_name, symbol FROM raw_announcements "
        "WHERE company_name IS NOT NULL",
    ):
        try:
            rows = conn.execute(query).fetchall()
        except sqlite3.OperationalError:
            continue
        for company_name, symbol in rows:
            key = _normalize_name(company_name)
            if key:
                out.setdefault(key, symbol)
    return out


def resolve_symbol(company: str, name_map: dict[str, str]) -> str | None:
    """Exact normalized match first, then unambiguous prefix containment."""
    key = _normalize_name(company)
    if not key:
        return None
    if key in name_map:
        return name_map[key]
    candidates = {sym for name, sym in name_map.items()
                  if name.startswith(key) or key.startswith(name)}
    return candidates.pop() if len(candidates) == 1 else None


# ============================================================================
# Pass orchestration (DB glue)
# ============================================================================

def _previous_reco(
    conn: sqlite3.Connection, symbol: str, brokerage_norm: str,
) -> tuple[str | None, float | None]:
    """(call, target) of OUR latest stored row for (symbol, ~brokerage)."""
    rows = conn.execute(
        "SELECT brokerage, new_call, new_target FROM raw_analyst_ratings "
        "WHERE symbol=? ORDER BY id DESC LIMIT 20",
        (symbol,),
    ).fetchall()
    for brokerage, call, target in rows:
        if _normalize_name(brokerage or "") == brokerage_norm:
            return call, target
    return None, None


def _published_recent(published_at: str | None, now: _dt.datetime) -> bool:
    if not published_at:
        return False
    try:
        dt = parsedate_to_datetime(published_at)
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=market_hours.IST)
    return (now - dt) <= _dt.timedelta(hours=_ALERT_RECENCY_HOURS)


def default_feed_fetcher(pages: int = _FEED_PAGES) -> list[dict]:
    """Pull + parse the live Trendlyne firehose (the injectable default). Newest
    first, so a couple of pages cover a trading day; polite 1s between pages."""
    import time as _time

    import httpx

    out: list[dict] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        resp = httpx.get(_FEED_URL, params={"page": page}, headers=_FEED_HEADERS,
                         timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        recos = parse_trendlyne(resp.text)
        if not recos:
            break                       # empty page → past the end
        for r in recos:
            key = item_fingerprint(r["title"], r.get("published_at") or "")
            if key not in seen:
                seen.add(key)
                out.append(r)
        if page < pages:
            _time.sleep(_PAGE_SLEEP)
    return out


def run_analyst_ratings_pass(
    conn: sqlite3.Connection,
    *,
    fetcher=None,
    tiers: dict[str, int] | None = None,
    now: _dt.datetime | None = None,
    emit: bool = True,
    sender=None,
) -> dict:
    """One poll of the feed: parse → store → signal tier-1 call changes.

    ``fetcher() -> [{title, link, published_at}]`` is injectable for tests;
    defaults to the live RSS pull. Idempotent via the item fingerprint."""
    now = now or market_hours.now_ist()
    tiers = tiers if tiers is not None else load_brokerage_tiers()
    try:
        items = (fetcher or default_feed_fetcher)()
    except Exception as e:  # noqa: BLE001 — a feed hiccup is a skipped poll, not a crash
        log.warning("analyst_feed_failed", error=str(e))
        return {"items": 0, "error": str(e)}

    name_map = build_company_symbol_map(conn)
    parsed = inserted = signaled = unresolved = 0
    for item in items:
        # Trendlyne items already carry structured fields (+ a reliable NSE symbol
        # from the link slug); legacy RSS items need the title regex + fuzzy match.
        reco = item if item.get("call") else parse_reco_title(item.get("title"))
        if reco is None:
            continue
        parsed += 1
        symbol = item.get("symbol") or resolve_symbol(reco["company"], name_map)
        if symbol is None:
            unresolved += 1
            log.info("analyst_symbol_unresolved", company=reco["company"])
            continue
        fingerprint = item_fingerprint(item["title"], item.get("published_at", ""))
        brokerage_norm = _normalize_name(reco["brokerage"])
        old_call, old_target = _previous_reco(conn, symbol, brokerage_norm)
        tier = match_brokerage_tier(reco["brokerage"], tiers)
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_analyst_ratings "
            "(symbol, brokerage, tier, old_call, new_call, old_target, new_target, "
            " published_at, source_url, fingerprint, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, reco["brokerage"], tier, old_call, reco["call"],
             old_target, reco["target"], item.get("published_at"),
             item.get("link"), fingerprint, int(time.time())),
        )
        if cur.rowcount == 0:        # already ingested this feed item
            continue
        inserted += 1
        if emit and tier == 1 and _published_recent(item.get("published_at"), now):
            if _emit_analyst_signal(
                conn, symbol=symbol, brokerage=reco["brokerage"],
                old_call=old_call, new_call=reco["call"],
                old_target=old_target, new_target=reco["target"],
                now=now, sender=sender,
            ):
                signaled += 1
    conn.commit()
    report = {"items": len(items), "parsed": parsed, "inserted": inserted,
              "unresolved": unresolved, "signaled": signaled}
    log.info("analyst_ratings_pass", **report)
    return report


# ============================================================================
# Tier-1 signal + alert (task 18.8)
# ============================================================================

def analyst_signal_type(old_call: str | None, new_call: str | None) -> str | None:
    """Upgrade/downgrade from the call-rank move; None when not a change.

    Needs BOTH calls (the first sighting of a brokerage's coverage isn't a
    change) and a genuine rank move (buy → buy with a new target isn't one)."""
    old_rank, new_rank = call_rank(old_call), call_rank(new_call)
    if old_rank is None or new_rank is None or old_rank == new_rank:
        return None
    return ANALYST_UPGRADE_T1 if new_rank > old_rank else ANALYST_DOWNGRADE_T1


def build_analyst_message(
    symbol: str, brokerage: str, signal_type: str,
    old_call, new_call, old_target, new_target,
) -> str:
    is_up = signal_type == ANALYST_UPGRADE_T1
    head = "Tier-1 Analyst Upgrade" if is_up else "Tier-1 Analyst Downgrade"
    emoji = "📈" if is_up else "📉"
    lines = [
        f"{emoji} {symbol} — {head}",
        f"{brokerage}: {(old_call or 'n/a').title()} → {(new_call or 'n/a').title()}",
    ]
    if old_target is not None or new_target is not None:
        old_s = f"₹{old_target:,.0f}" if old_target is not None else "n/a"
        new_s = f"₹{new_target:,.0f}" if new_target is not None else "n/a"
        lines.append(f"Target: {old_s} → {new_s}")
    return "\n".join(lines)


def _emit_analyst_signal(
    conn: sqlite3.Connection, *, symbol: str, brokerage: str,
    old_call, new_call, old_target, new_target,
    now: _dt.datetime, sender=None,
) -> bool:
    """Write the tier-1 signal row + send the alert. True only when alerted.

    dispatched=1: the alert is sent here (like the credit alerts), so the
    generic dispatcher must never re-process the row."""
    sig_type = analyst_signal_type(old_call, new_call)
    if sig_type is None:
        return False
    direction = "long" if sig_type == ANALYST_UPGRADE_T1 else "short"
    conn.execute(
        "INSERT INTO signals (symbol, signal_type, detected_at, direction, "
        " dispatched, dispatched_at) VALUES (?, ?, ?, ?, 1, ?)",
        (symbol, sig_type, now.isoformat(), direction, now.isoformat()),
    )
    if sender is None:
        return False
    import os

    from ..bot.dispatcher import load_telegram_config
    token, chat_id = load_telegram_config()
    topic = os.environ.get("TELEGRAM_TOPIC_ANALYST")
    thread_id = int(topic) if topic and topic.lstrip("-").isdigit() else None
    text = build_analyst_message(
        symbol, brokerage, sig_type, old_call, new_call, old_target, new_target,
    )
    return bool(sender(token, chat_id, text, thread_id))


# ============================================================================
# Scheduling (30-min cadence, trading-day morning→close window)
# ============================================================================

def _in_polling_window(now: _dt.datetime) -> bool:
    if not market_hours.is_trading_day(now.date()):
        return False
    t = (now.hour, now.minute)
    return _WINDOW_START <= t <= _WINDOW_END


def run_analyst_ratings_job(db_path: str) -> dict:
    from ..bot.dispatcher import send_telegram
    from ..storage.db import open_db

    now = market_hours.now_ist()
    if not _in_polling_window(now):
        return {"skipped": "off_window"}
    conn = open_db(db_path)
    try:
        return run_analyst_ratings_pass(conn, now=now, sender=send_telegram)
    finally:
        conn.close()


def register_analyst_ratings_job(scheduler, db_path: str) -> str:
    """Every 30 min, gated to trading days 08:30–15:35 IST internally."""
    from apscheduler.triggers.interval import IntervalTrigger

    def _tick():
        try:
            report = run_analyst_ratings_job(db_path)
            if "skipped" not in report:
                log.info("analyst_ratings_tick", **report)
        except Exception:
            log.exception("analyst_ratings_failed")

    scheduler.add_job(
        _tick, trigger=IntervalTrigger(seconds=_INTERVAL_SECONDS),
        id=JOB_ID, max_instances=1, coalesce=True, replace_existing=True,
    )
    return JOB_ID
