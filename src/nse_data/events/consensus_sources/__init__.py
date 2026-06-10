"""Live consensus-estimate sources (P6 / Week 17.5 S8).

Four sources feed the source-agnostic ``consensus_estimates`` table (the
user's call, 2026-06: implement them all, accuracy over cost):

  * ``manual``       — broker-preview numbers via scripts/load_consensus.py
                       (CSV / single-row); the most accurate NII/NIM path.
  * ``news``         — broker previews read out of news articles (Bing News
                       RSS → publisher page → LLM extraction); the automated
                       NII/NIM path.
  * ``moneycontrol`` — quarterly earning-forecast API (rev + PAT + EPS, ₹ cr).
  * ``yahoo``        — earningsTrend quoteSummary (EPS + revenue, INR).

Lookup merges field-wise in accuracy order (``consensus.SOURCE_RANK``):
manual → news → moneycontrol → yahoo. With several live sources the numbers
cross-validate — ``consensus.estimates_by_source`` shows them side by side.

Each fetcher is a plain ``fetcher(symbol) -> list[dict]`` for
``estimate_scraper.fetch_and_ingest``; parsing is pure-function and tested
offline, all I/O degrades per-symbol (one bad symbol never aborts a batch).
"""
from .moneycontrol import make_moneycontrol_fetcher, parse_earning_forecast
from .news import make_news_fetcher, parse_bing_rss
from .yahoo import make_yahoo_fetcher, parse_quote_summary

__all__ = [
    "make_moneycontrol_fetcher",
    "make_news_fetcher",
    "make_yahoo_fetcher",
    "parse_bing_rss",
    "parse_earning_forecast",
    "parse_quote_summary",
]
