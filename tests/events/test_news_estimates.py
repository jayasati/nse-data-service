"""P6 news source — Bing RSS parsing, article reading, sanity band, and the
field-wise merge with the other sources. Offline: fixtures mirror the live
Bing RSS shape captured 2026-06-10; the LLM read is faked.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

import pytest

from nse_data.events import consensus
from nse_data.events.consensus_sources import news
from nse_data.events.estimate_scraper import fetch_and_ingest
from nse_data.storage.db import apply_migrations

APICLICK = ("http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&tid=x"
            "&url=https%3a%2f%2fwww.business-standard.com%2fmarkets%2fsbi-preview.html&c=1&mkt=en-in")

# In the feed, URLs are XML-escaped (& → &amp;), as the live feed does.
RSS = f"""<?xml version="1.0" encoding="utf-8" ?><rss version="2.0"><channel>
<title>q - BingNews</title><link>https://www.bing.com/news/search?q=q</link>
<item><title>SBI Q4 results preview: profit may drop</title><link>{APICLICK.replace("&", "&amp;")}</link></item>
<item><title>Direct link item</title><link>https://www.livemint.com/markets/sbi-q4-preview.html</link></item>
<item><title>Bing internal</title><link>https://www.bing.com/news/search?q=other&amp;x=1</link></item>
</channel></rss>"""


# --- pure parsing ---------------------------------------------------------------

def test_publisher_url_decodes_apiclick():
    assert news.publisher_url(APICLICK) == \
        "https://www.business-standard.com/markets/sbi-preview.html"
    assert news.publisher_url("https://www.livemint.com/a.html") == \
        "https://www.livemint.com/a.html"
    assert news.publisher_url("https://www.bing.com/news/search?q=x") is None
    assert news.publisher_url("") is None


def test_parse_bing_rss_keeps_publisher_items_only():
    items = news.parse_bing_rss(RSS)
    assert [i["url"] for i in items] == [
        "https://www.business-standard.com/markets/sbi-preview.html",
        "https://www.livemint.com/markets/sbi-q4-preview.html",
    ]
    assert news.parse_bing_rss("not xml") == []


def test_html_text_strips_chrome():
    html = ("<html><head><script>var x=1;</script><style>.a{}</style></head>"
            "<body><nav>menu</nav><p>NII seen at &#x20b9;44,000 crore</p></body></html>")
    assert news.html_text(html) == "NII seen at ₹44,000 crore"


# --- quarter + sanity ------------------------------------------------------------

@pytest.mark.parametrize("event,expected", [
    ("2026-07-15", "2026-06-30"),   # July filing reports the June quarter
    ("2026-05-08", "2026-03-31"),   # the SBI case
    ("2026-01-20", "2025-12-31"),
    ("2026-10-15", "2026-09-30"),
])
def test_quarter_end_before(event, expected):
    assert news.quarter_end_before(_dt.date.fromisoformat(event)).isoformat() == expected


def test_sanity_filter_drops_out_of_band_fields():
    est = {"nii_est_cr": 440000.0, "pat_est_cr": 18500.0}   # NII 10× off (unit slip)
    year_ago = {"net_interest_income_cr": 41620.0, "pat_cr": 18640.0}
    out = news.sanity_filter(est, year_ago, symbol="SBIN")
    assert out["nii_est_cr"] is None        # dropped
    assert out["pat_est_cr"] == 18500.0     # kept
    # no year-ago data → nothing to judge against, keep the value
    assert news.sanity_filter({"pat_est_cr": 9.9}, {}, symbol="X")["pat_est_cr"] == 9.9


def test_coerce_requires_preview_framing():
    assert news._coerce({"is_preview": False, "pat_est_cr": 100}) is None
    assert news._coerce({"pat_est_cr": 100}) is None
    got = news._coerce({"is_preview": True, "pat_est_cr": 100, "nim_est_pct": 95.0,
                        "eps_est": "12"})
    assert got == {"rev_est_cr": None, "pat_est_cr": 100.0, "eps_est": None,
                   "nii_est_cr": None, "nim_est_pct": None}   # 95% NIM = junk


# --- the fetcher end-to-end (faked I/O + LLM) --------------------------------------

class FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status = text, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"http {self.status}")


class FakeClient:
    def __init__(self, pages):
        self.pages = pages   # url-substring → FakeResp

    def get(self, url, params=None):
        if params and params.get("format") == "rss":
            return FakeResp(RSS)
        for frag, resp in self.pages.items():
            if frag in url:
                return resp
        return FakeResp("", 404)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    c.execute("INSERT INTO pending_events (symbol, event_type, expected_date, status, created_at) "
              "VALUES ('SBIN', 'result', '2026-07-25', 'upcoming', 0)")
    # year-ago actuals for the sanity band (Jun-2025 quarter)
    c.execute("INSERT INTO extracted_financials (symbol, period_ending, scope, revenue_cr, "
              "pat_cr, net_interest_income_cr, extracted_at) "
              "VALUES ('SBIN', '2025-06-30', 'standalone', 115000, 17000, 41620, 0)")
    c.commit()
    return c


def test_news_fetcher_averages_articles_and_targets_event_quarter(conn, monkeypatch):
    monkeypatch.setattr(news, "_SLEEP_BETWEEN_CALLS", 0)
    reads = {"business-standard": {"nii_est_cr": 44000.0, "pat_est_cr": 18000.0},
             "livemint": {"nii_est_cr": 45000.0, "pat_est_cr": None}}

    def fake_llm(text, *, symbol, company, period):
        assert period.isoformat() == "2026-06-30"   # from the pending event
        for frag, est in reads.items():
            if frag in text:
                return {**{k: None for k in news._EST_FIELDS}, **est}
        return None

    monkeypatch.setattr(news, "extract_estimates_llm", fake_llm)
    client = FakeClient({
        "business-standard": FakeResp("<p>business-standard preview</p>"),
        "livemint": FakeResp("<p>livemint preview</p>"),
    })
    fetch = news.make_news_fetcher(conn, client)
    recs = fetch("SBIN")
    assert len(recs) == 1
    r = recs[0]
    assert r["period_ending"] == "2026-06-30"
    assert r["nii_est_cr"] == 44500.0          # mean of the two articles
    assert r["pat_est_cr"] == 18000.0          # only one article carried it


def test_news_fetcher_survives_paywalled_article(conn, monkeypatch):
    monkeypatch.setattr(news, "_SLEEP_BETWEEN_CALLS", 0)
    monkeypatch.setattr(news, "extract_estimates_llm",
                        lambda text, **kw: {**{k: None for k in news._EST_FIELDS},
                                            "nii_est_cr": 44000.0})
    client = FakeClient({
        "business-standard": FakeResp("", status=403),   # paywalled
        "livemint": FakeResp("<p>preview</p>"),
    })
    recs = news.make_news_fetcher(conn, client)("SBIN")
    assert len(recs) == 1 and recs[0]["nii_est_cr"] == 44000.0


def test_news_fetcher_empty_when_no_preview_found(conn, monkeypatch):
    monkeypatch.setattr(news, "_SLEEP_BETWEEN_CALLS", 0)
    monkeypatch.setattr(news, "extract_estimates_llm", lambda text, **kw: None)
    client = FakeClient({"business-standard": FakeResp("<p>x</p>"),
                         "livemint": FakeResp("<p>y</p>")})
    assert news.make_news_fetcher(conn, client)("SBIN") == []


# --- merge with the other sources ---------------------------------------------------

def test_news_nii_merges_with_mc_pat(conn, monkeypatch):
    monkeypatch.setattr(news, "_SLEEP_BETWEEN_CALLS", 0)
    monkeypatch.setattr(news, "extract_estimates_llm",
                        lambda text, **kw: {**{k: None for k in news._EST_FIELDS},
                                            "nii_est_cr": 44500.0, "nim_est_pct": 3.0})
    client = FakeClient({"business-standard": FakeResp("<p>p</p>"),
                         "livemint": FakeResp("<p>p</p>")})
    fetch_and_ingest(conn, ["SBIN"], fetcher=news.make_news_fetcher(conn, client),
                     source="news")
    consensus.upsert_estimate(conn, symbol="SBIN", period_ending="2026-06-30",
                              pat_est_cr=18400.0, rev_est_cr=118000.0,
                              source="moneycontrol")
    est = consensus.nearest_estimate(conn, "SBIN", "2026-06-30")
    assert est["source"] == "news+moneycontrol"
    assert est["nii_est_cr"] == 44500.0 and est["nim_est_pct"] == 3.0   # from news
    assert est["pat_est_cr"] == 18400.0 and est["rev_est_cr"] == 118000.0  # from MC