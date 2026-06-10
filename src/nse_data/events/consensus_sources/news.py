"""Broker-preview estimates from news articles (P6, source='news').

The only automated path to **bank NII/NIM estimates**: before a big result,
broker "result preview" notes get republished by Business Standard /
Moneycontrol / Mint / ET, with the numbers in prose ("analysts expect NII of
₹44,000 crore, NIM at 3.0%"). This source finds those articles and reads them:

  1. **Search** — Bing News RSS (``bing.com/news/search?format=rss``): unlike
     Google News (opaque redirect links + personal-reader-only ToS), Bing's
     item links carry the publisher URL verbatim in the ``url=`` param.
     Verified live 2026-06-10 (the real SBI Q4 FY26 preview from
     business-standard surfaced with NII/dividend estimates).
  2. **Fetch** the publisher article (paywalled pages simply yield nothing —
     an accepted miss, never worked around).
  3. **LLM-extract** with the same discipline as the P7 narrative read: JSON
     mode, null unless the number is explicitly framed as an *expectation*
     (an article reporting declared results must return all nulls), ₹ crore.
  4. **Sanity-check vs the year-ago actual** from extracted_financials — an
     "estimate" more than ~2.5× away from last year's printed number is a
     misread (wrong row, wrong unit, wrong quarter) and the field is dropped.
  5. **Average across articles** (different articles quote different brokers;
     the mean is closer to a consensus than any single note).

Accuracy ranking: below your hand-entered ``manual``, above the aggregators
for the fields only it carries (``consensus.SOURCE_RANK``; lookup merges
field-wise, so news NII never masks Moneycontrol's PAT).
"""
from __future__ import annotations

import datetime as _dt
import re
import sqlite3
import time
import urllib.parse
import xml.etree.ElementTree as ET

import structlog

log = structlog.get_logger()

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# Full browser-shaped headers: several publishers (business-standard, zeebiz)
# 403 a bare UA but serve a normally-furnished request. Sites that still
# refuse are accepted misses — never worked around beyond ordinary headers.
_HEADERS = {
    "User-Agent": _UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.bing.com/news/search",
}
_RSS_URL = "https://www.bing.com/news/search"
_TIMEOUT = 15.0
_SLEEP_BETWEEN_CALLS = 1.0
_MAX_ARTICLES = 3
_MAX_TEXT_CHARS = 20_000
# Plausibility band vs the year-ago actual (mirrors the vision extractor's
# comparative check): outside this, the "estimate" is a misread, not a view.
_SANE_LO, _SANE_HI = 0.4, 2.5

_EST_FIELDS = ("rev_est_cr", "pat_est_cr", "eps_est", "nii_est_cr", "nim_est_pct")
# Fields cross-checked against extracted_financials year-ago levels.
_SANITY = (("rev_est_cr", "revenue_cr"), ("pat_est_cr", "pat_cr"),
           ("nii_est_cr", "net_interest_income_cr"))

_PROMPT = """\
You are reading a news article about {company} ({symbol})'s UPCOMING quarterly \
result for the quarter ended {period} ({qlabel}).

Extract ONLY analyst/brokerage EXPECTATIONS for that quarter — numbers framed \
as "expect", "estimate", "likely", "may report", "poll", "preview". Rules:
- If the article reports DECLARED results (actuals), return every field null.
- If the article is about a different company or a different quarter, return \
every field null.
- Never use year-ago/last-quarter actuals mentioned for comparison.
- Convert to the requested units. Do not compute anything else.

Return a JSON object with exactly these keys (null when not stated):
  "rev_est_cr":  number | null — expected revenue / total income, INR crore
  "pat_est_cr":  number | null — expected net profit (PAT), INR crore
  "eps_est":     number | null — expected EPS, INR per share
  "nii_est_cr":  number | null — expected net interest income, INR crore (banks)
  "nim_est_pct": number | null — expected net interest margin, percent (banks)
  "is_preview":  true | false  — whether this article is a pre-result preview

--- ARTICLE TEXT ---
{text}"""


# --------------------------------------------------------------------------- #
# search + fetch (pure parsing split out for offline tests)
# --------------------------------------------------------------------------- #

def publisher_url(link: str) -> str | None:
    """Bing item link → the publisher URL (from apiclick's ``url=`` param, or
    the link itself when direct). Bing-internal links return None."""
    if not link:
        return None
    parsed = urllib.parse.urlparse(link)
    if "bing.com" in parsed.netloc:
        url = urllib.parse.parse_qs(parsed.query).get("url", [None])[0]
        if not url:
            return None
        parsed = urllib.parse.urlparse(url)
        link = url
    return link if parsed.scheme in ("http", "https") and "bing.com" not in parsed.netloc else None


def parse_bing_rss(xml_text: str) -> list[dict]:
    """RSS XML → [{title, url}] with publisher URLs only, feed order kept."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[dict] = []
    for item in root.iter("item"):
        url = publisher_url((item.findtext("link") or "").strip())
        if url:
            out.append({"title": (item.findtext("title") or "").strip(), "url": url})
    return out


_TAG_DROP = re.compile(r"(?is)<(script|style|noscript|svg|header|footer|nav)\b.*?</\1>")
_TAG = re.compile(r"(?s)<[^>]+>")


def html_text(html: str) -> str:
    """Crude but dependency-free article text: drop script/style/nav blocks,
    strip tags, collapse whitespace."""
    txt = _TAG.sub(" ", _TAG_DROP.sub(" ", html or ""))
    txt = (txt.replace("&amp;", "&").replace("&nbsp;", " ")
              .replace("&lt;", "<").replace("&gt;", ">").replace("&#x20b9;", "₹")
              .replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\s+", " ", txt).strip()


# --------------------------------------------------------------------------- #
# the target quarter + sanity data, from our own DB
# --------------------------------------------------------------------------- #

_QLABEL = {6: "Q1", 9: "Q2", 12: "Q3", 3: "Q4"}   # Indian FY quarters


def quarter_end_before(d: _dt.date) -> _dt.date:
    """The most recent quarter-end strictly before ``d`` — the quarter a
    result filed on ``d`` reports."""
    y, m = d.year, d.month
    for qm in (12, 9, 6, 3):
        if qm < m or (qm == 12 and m <= 3):
            qy = y - 1 if qm == 12 and m <= 3 else y
            nxt = _dt.date(qy + (qm == 12), qm % 12 + 1, 1)
            return nxt - _dt.timedelta(days=1)
    return _dt.date(y - 1, 12, 31)


def _target_period(conn: sqlite3.Connection, symbol: str) -> _dt.date:
    """The quarter the symbol's next result reports: from its upcoming
    pending_event, else the last completed quarter."""
    try:
        row = conn.execute(
            "SELECT MIN(expected_date) FROM pending_events "
            "WHERE symbol=? AND status='upcoming' AND event_type='result' "
            "AND expected_date >= date('now')",
            (symbol,),
        ).fetchone()
        if row and row[0]:
            return quarter_end_before(_dt.date.fromisoformat(row[0]))
    except sqlite3.OperationalError:
        pass
    return quarter_end_before(_dt.date.today())


def _company_name(conn: sqlite3.Connection, symbol: str) -> str:
    try:
        row = conn.execute(
            "SELECT company_name FROM raw_announcements WHERE symbol=? "
            "AND company_name IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return row[0] if row and row[0] else symbol
    except sqlite3.OperationalError:
        return symbol


def _year_ago_levels(conn: sqlite3.Connection, symbol: str, period: _dt.date) -> dict:
    """Year-ago actuals (±25d on the period) for the sanity band."""
    try:
        rows = conn.execute(
            "SELECT period_ending, revenue_cr, pat_cr, net_interest_income_cr "
            "FROM extracted_financials WHERE symbol=?",
            (symbol,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    target = period.replace(year=period.year - 1)
    for pe, rev, pat, nii in rows:
        try:
            d = _dt.date.fromisoformat(str(pe)[:10])
        except ValueError:
            continue
        if abs((d - target).days) <= 25:
            return {"revenue_cr": rev, "pat_cr": pat, "net_interest_income_cr": nii}
    return {}


def sanity_filter(est: dict, year_ago: dict, *, symbol: str) -> dict:
    """Drop any estimate wildly outside the band around the year-ago actual."""
    out = dict(est)
    for est_key, actual_key in _SANITY:
        v, base = out.get(est_key), year_ago.get(actual_key)
        if v is None or not isinstance(base, (int, float)) or base <= 0:
            continue
        if not (_SANE_LO * base <= v <= _SANE_HI * base):
            log.warning("news_estimate_insane", symbol=symbol, field=est_key,
                        value=v, year_ago=base)
            out[est_key] = None
    return out


# --------------------------------------------------------------------------- #
# LLM extraction
# --------------------------------------------------------------------------- #

def _coerce(parsed: dict) -> dict | None:
    """Validate the LLM JSON; non-previews and junk values become nothing."""
    if not isinstance(parsed, dict) or parsed.get("is_preview") is not True:
        return None
    out: dict = {}
    for k in _EST_FIELDS:
        v = parsed.get(k)
        out[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    if out.get("nim_est_pct") is not None and not (0 < out["nim_est_pct"] < 25):
        out["nim_est_pct"] = None
    return out if any(v is not None for v in out.values()) else None


def extract_estimates_llm(
    text: str, *, symbol: str, company: str, period: _dt.date,
) -> dict | None:
    """One JSON-mode read of one article. None when the LLM is unconfigured,
    the article isn't a preview, or nothing was stated."""
    from ...parsers.narrative.llm_narrative import _get_client

    client = _get_client()
    if client is None or not text:
        return None
    prompt = _PROMPT.format(
        company=company, symbol=symbol, period=period.isoformat(),
        qlabel=_QLABEL.get(period.month, ""), text=text[:_MAX_TEXT_CHARS],
    )
    try:
        res = client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}, max_tokens=300,
        )
    except Exception as e:  # noqa: BLE001 — incl. DailyCapExceeded
        log.warning("news_llm_failed", symbol=symbol, error=str(e))
        return None
    if not res.success or not isinstance(res.parsed_json, dict):
        return None
    return _coerce(res.parsed_json)


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 2)


def make_news_fetcher(
    conn: sqlite3.Connection, client=None, *, max_articles: int = _MAX_ARTICLES,
):
    """A ``fetcher(symbol) -> list[dict]`` for ``fetch_and_ingest``.

    Needs the DB connection (target quarter from pending_events, company name,
    year-ago sanity levels). Returns at most one record per symbol — the
    field-wise mean of every preview article that yielded numbers."""
    if client is None:
        import httpx

        client = httpx.Client(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)

    def fetch(symbol: str) -> list[dict]:
        period = _target_period(conn, symbol)
        company = _company_name(conn, symbol)
        query = f"{company} {_QLABEL.get(period.month, '')} results preview estimates"
        time.sleep(_SLEEP_BETWEEN_CALLS)
        r = client.get(_RSS_URL, params={"q": query, "format": "rss"})
        r.raise_for_status()
        articles = parse_bing_rss(r.text)

        reads: list[dict] = []
        seen_hosts: set[str] = set()
        for art in articles:
            if len(reads) >= max_articles:
                break
            host = urllib.parse.urlparse(art["url"]).netloc
            if host in seen_hosts:
                continue   # one read per publisher — diversity over repetition
            seen_hosts.add(host)
            try:
                time.sleep(_SLEEP_BETWEEN_CALLS)
                page = client.get(art["url"])
                page.raise_for_status()
            except Exception as e:  # noqa: BLE001 — paywalls/404s are accepted misses
                log.info("news_article_skipped", symbol=symbol, url=art["url"], error=str(e))
                continue
            est = extract_estimates_llm(
                html_text(page.text), symbol=symbol, company=company, period=period,
            )
            if est:
                reads.append(est)

        if not reads:
            return []
        merged = {k: _mean(vs) for k in _EST_FIELDS
                  if (vs := [r[k] for r in reads if r.get(k) is not None])}
        merged = sanity_filter(merged, _year_ago_levels(conn, symbol, period), symbol=symbol)
        if not any(merged.get(k) is not None for k in _EST_FIELDS):
            return []
        log.info("news_estimates", symbol=symbol, period=period.isoformat(),
                 articles=len(reads), fields=[k for k, v in merged.items() if v is not None])
        return [{"symbol": symbol, "period_ending": period.isoformat(), **merged}]

    return fetch
