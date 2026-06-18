"""Persistent per-stock news memory (plans/NEWS_MEMORY_PLAN.md).

Two layers feed `raw_news`:
  - HEADLINE layer: Google News RSS, queried PER SYMBOL (symbol is known, no
    resolution needed). Broad coverage; headline+url+date, no body (the links are
    opaque Google redirects — bodies aren't fetchable from here).
  - FULL-TEXT layer: direct publisher RSS (ET Markets, Moneycontrol) → direct
    article URLs → clean body via bs4. Market-wide feeds, so the company is
    entity-resolved to a symbol.

Pure-ish functions + a store; the orchestration/cadence lives in
scripts/collect_news.py. published_epoch is INT epoch (never a sortable string).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import structlog

from .move_causes import parse_gnews_rss

log = structlog.get_logger()

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_GNEWS = "https://news.google.com/rss/search"

# direct publisher RSS feeds → full article bodies (verified 2026-06-18)
PUBLISHER_FEEDS = {
    "et_markets": "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146843.cms",
    "moneycontrol": "https://www.moneycontrol.com/rss/business.xml",
}


def make_client(timeout: float = 20.0):
    import httpx
    return httpx.Client(headers={"User-Agent": _UA}, timeout=timeout, follow_redirects=True)


def news_epoch(s: str | None) -> int | None:
    """RSS date → IST epoch seconds. Handles RFC822 (publisher feeds) and the
    'DD Mon YYYY' / ISO shapes Google News emits. None if unparseable."""
    if not s:
        return None
    s = s.strip()
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_IST)
            return int(dt.timestamp())
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d-%b-%Y"):
        try:
            return int(_dt.datetime.strptime(s[:11].strip(), fmt).replace(tzinfo=_IST).timestamp())
        except ValueError:
            continue
    return None


def fingerprint(symbol: str | None, url: str | None, headline: str) -> str:
    key = f"{symbol or ''}|{url or headline}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def parse_publisher_feed(xml_text: str) -> list[dict]:
    """Generic RSS → [{headline, url, published_raw, snippet}] (lenient)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        out.append({
            "headline": title, "url": link,
            "published_raw": (it.findtext("pubDate") or "").strip(),
            "snippet": re.sub(r"<[^>]+>", "", it.findtext("description") or "").strip()[:500],
        })
    return out


def extract_article_text(html: str) -> str:
    """Body text from a publisher page (bs4): strip chrome, join substantial <p>."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        t.decompose()
    ps = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    return " ".join(p for p in ps if len(p) > 40)


def fetch_article_text(client, url: str) -> tuple[str, str]:
    """(text, status). status: ok | paywalled | failed."""
    try:
        r = client.get(url)
        body = extract_article_text(r.text)
        if len(body) > 600:
            return body, "ok"
        low = r.text.lower()
        if "subscrib" in low or "sign in to read" in low:
            return body, "paywalled"
        return body, "failed"
    except Exception as e:  # noqa: BLE001
        log.info("article_fetch_failed", url=url[:80], error=str(e))
        return "", "failed"


def fetch_google_news(client, query: str, limit: int = 50) -> list[dict]:
    """Per-symbol headline layer (no body)."""
    try:
        r = client.get(_GNEWS, params={"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
        arts = parse_gnews_rss(r.text)
    except Exception as e:  # noqa: BLE001
        log.info("gnews_failed", query=query, error=str(e))
        return []
    out = []
    for a in arts[:limit]:
        out.append({
            "headline": a["title"], "url": a.get("url"),
            "published_raw": a["date"].isoformat() if a.get("date") else None,
            "snippet": None,
        })
    return out


def build_name_map(conn) -> dict[str, str]:
    """{normalized company name → symbol} for entity resolution, from tables that
    carry both. Longest names first so specific matches win over generic ones."""
    out: dict[str, str] = {}
    for sql in (
        "SELECT DISTINCT company_name, symbol FROM raw_quote_metadata WHERE company_name IS NOT NULL",
        "SELECT DISTINCT company_name, symbol FROM raw_board_meetings WHERE company_name IS NOT NULL",
        "SELECT DISTINCT company_name, symbol FROM raw_announcements WHERE company_name IS NOT NULL",
    ):
        try:
            for name, sym in conn.execute(sql):
                k = _norm(name)
                if k and len(k) > 3:
                    out.setdefault(k, sym)
                # also match the ticker itself in a headline ("HFCL jumps 5%"),
                # but only len>=4 tickers — short ones cause false positives.
                if sym and len(sym) >= 4 and sym.isalpha():
                    out.setdefault(sym.lower(), sym)
        except Exception:  # noqa: BLE001
            continue
    return out


_NOISE = re.compile(r"\b(limited|ltd|india|corporation|corp|company|co|industries|enterprises)\b\.?",
                    re.IGNORECASE)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", _NOISE.sub(" ", s or "").lower()).strip()


def resolve_symbol(headline: str, name_map: dict[str, str]) -> str | None:
    """Symbol whose company name appears in the headline (longest match wins)."""
    h = _norm(headline)
    if not h:
        return None
    best = None
    for name, sym in name_map.items():
        if name in h and (best is None or len(name) > len(best[0])):
            best = (name, sym)
    return best[1] if best else None


def store_news(conn, rows: list[dict]) -> int:
    """INSERT OR IGNORE; returns new rows. Each row: symbol, headline, url, source,
    published_raw, snippet, article_text, text_status."""
    import time
    now = int(time.time())
    n = 0
    for r in rows:
        fp = fingerprint(r.get("symbol"), r.get("url"), r["headline"])
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_news (fingerprint, symbol, headline, url, source, "
            "published_epoch, published_raw, snippet, article_text, text_status, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (fp, r.get("symbol"), r["headline"], r.get("url"), r.get("source"),
             news_epoch(r.get("published_raw")), r.get("published_raw"), r.get("snippet"),
             r.get("article_text"), r.get("text_status", "headline_only"), now))
        n += cur.rowcount
    conn.commit()
    return n
