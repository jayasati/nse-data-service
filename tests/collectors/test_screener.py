"""
Acceptance tests for the ScreenerFundamentals collector (screener.in scrape).

External fan-out over the watchlist; run() is overridden. We test the regex
parsing against a fixture mirroring screener's real markup (#top-ratios ul +
ranges-table CAGR tables), and run() with watchlist()/fetch() overridden so no
network is touched. now_ist patched for a deterministic as_of_date.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from nse_data.collectors import screener as screener_mod
from nse_data.collectors.base import Request
from nse_data.collectors.screener import ScreenerFundamentals, ScreenerParseError
from nse_data.scheduler.market_hours import IST


FIXTURE = (Path(__file__).parent.parent / "fixtures" / "screener_company.html")
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(
        screener_mod, "now_ist", lambda: datetime(2026, 5, 26, 8, 30, tzinfo=IST)
    )


@pytest.fixture
def html():
    return FIXTURE.read_text()


# ============================================================================
# normalize() — regex parsing
# ============================================================================

def test_normalize_extracts_ratios_and_cagr(html):
    row = ScreenerFundamentals().normalize(
        html, Request(path_or_url="x", meta={"symbol": "RELIANCE"})
    )[0]
    assert row["symbol"] == "RELIANCE"
    assert row["as_of_date"] == "2026-05-26"
    assert row["roce"] == 7.89
    assert row["roe"] == 8.51
    assert row["stock_pe"] == 22.51
    assert row["market_cap"] == 1908123.0       # commas stripped
    assert row["book_value"] == 623.0
    assert row["dividend_yield"] == 0.41
    assert row["debt_to_equity"] == 0.38
    assert row["sales_cagr_3y"] == -2.0          # negative CAGR
    assert row["profit_cagr_3y"] == 11.0
    assert row["source_url"].endswith("/company/RELIANCE/")


def test_partial_page_still_returns_row(html):
    """A company genuinely missing one metric (CAGR table absent) still yields
    a row — the guard only fires on full markup drift, not sparse data."""
    no_cagr = html.split('<table class="ranges-table">')[0]  # drop CAGR tables
    row = ScreenerFundamentals().normalize(
        no_cagr, Request(path_or_url="x", meta={"symbol": "RELIANCE"})
    )[0]
    assert row["roce"] == 7.89
    assert row["sales_cagr_3y"] is None


# ---- parse-health guard: drift fails loudly, not silently ----

def test_guard_raises_when_top_ratios_absent():
    with pytest.raises(ScreenerParseError):
        ScreenerFundamentals().normalize(
            "<html><body>no ratios here (page changed/blocked)</body></html>",
            Request(path_or_url="x", meta={"symbol": "FOO"}),
        )


def test_guard_raises_when_section_present_but_unparseable():
    """#top-ratios block exists but class names changed -> nothing parsed."""
    drifted = '<ul id="top-ratios"><li><b>ROCE</b> 7.89%</li></ul>'  # no name/number spans
    with pytest.raises(ScreenerParseError):
        ScreenerFundamentals().normalize(
            drifted, Request(path_or_url="x", meta={"symbol": "FOO"})
        )


def test_normalize_empty_input():
    c = ScreenerFundamentals()
    assert c.normalize("", Request(path_or_url="x")) == []
    assert c.normalize(None, Request(path_or_url="x")) == []


# ============================================================================
# watchlist() loading
# ============================================================================

def test_watchlist_reads_universe_yaml():
    wl = ScreenerFundamentals().watchlist()
    assert "RELIANCE" in wl and "HDFCBANK" in wl


def test_watchlist_missing_file_is_empty():
    c = ScreenerFundamentals()
    c.universe_path = "config/does_not_exist.yaml"
    assert c.watchlist() == []


# ============================================================================
# run() — external fan-out, no network
# ============================================================================

def _collector(html_by_symbol: dict, errors: dict | None = None):
    errors = errors or {}

    class _TestScreener(ScreenerFundamentals):
        request_delay = 0

        def watchlist(self):
            return list(html_by_symbol.keys())

        def fetch(self, client, symbol):
            if symbol in errors:
                raise errors[symbol]
            return html_by_symbol[symbol]

    return _TestScreener()


def test_run_persists_watchlist(db, html):
    c = _collector({"RELIANCE": html, "TCS": html})
    report = c.run(session=None, db=db)
    assert report.fetched == 2
    assert report.persist.inserted == 2
    n = db.execute("SELECT COUNT(*) FROM raw_fundamentals_screener").fetchone()[0]
    assert n == 2


def test_run_isolates_failing_symbol(db, html):
    c = _collector({"RELIANCE": html, "TCS": html}, errors={"TCS": RuntimeError("403")})
    report = c.run(session=None, db=db)
    assert report.succeeded == 1
    assert report.failed == 1
    assert {r[0] for r in db.execute("SELECT symbol FROM raw_fundamentals_screener")} == {"RELIANCE"}


def test_rerun_same_week_upserts(db, html):
    c = _collector({"RELIANCE": html})
    c.run(session=None, db=db)
    c.run(session=None, db=db)
    assert db.execute("SELECT COUNT(*) FROM raw_fundamentals_screener").fetchone()[0] == 1
