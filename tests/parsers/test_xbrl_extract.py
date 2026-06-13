"""XBRL-first extraction resolver + extractor (parsers/xbrl_extract.py)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nse_data.parsers import xbrl_extract as xe

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript((MIGRATIONS / "008_phase7_day3.sql").read_text())   # raw_financial_results
    c.executescript((MIGRATIONS / "016_integrated_filings.sql").read_text())  # raw_integrated_filings
    return c


def _ifiling(c, symbol, qe, scope, url, created=1):
    c.execute(
        "INSERT INTO raw_integrated_filings (fingerprint, filing_type, type_sub, "
        "symbol, qe_date, consolidated, xbrl_url, created_at) "
        "VALUES (?, 'Integrated Filing- Financials', 'Original', ?, ?, ?, ?, ?)",
        (f"{symbol}-{qe}-{scope}-{created}", symbol, qe, scope, url, created),
    )
    c.commit()


def test_resolve_picks_quarter_and_scopes(conn):
    _ifiling(conn, "ACME", "31-MAR-2026", "Standalone", "http://x/sa.xml")
    _ifiling(conn, "ACME", "31-MAR-2026", "Consolidated", "http://x/con.xml")
    _ifiling(conn, "ACME", "31-DEC-2025", "Standalone", "http://x/old.xml")  # wrong quarter
    urls = xe.resolve_xbrl_urls(conn, "ACME", "30-Apr-2026 17:00:00")
    assert urls == {"standalone": "http://x/sa.xml", "consolidated": "http://x/con.xml"}


def test_resolve_latest_revision_wins(conn):
    _ifiling(conn, "ACME", "31-MAR-2026", "Standalone", "http://x/orig.xml", created=1)
    _ifiling(conn, "ACME", "31-MAR-2026", "Standalone", "http://x/rev.xml", created=2)
    urls = xe.resolve_xbrl_urls(conn, "ACME", "30-Apr-2026")
    assert urls["standalone"] == "http://x/rev.xml"


def test_resolve_none_when_no_filing(conn):
    assert xe.resolve_xbrl_urls(conn, "NOPE", "30-Apr-2026") == {
        "standalone": None, "consolidated": None}


# A minimal INDAS-style XBRL the real parser understands (quarter context OneD).
_XBRL = b"""<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:in="http://x">
 <xbrli:context id="OneD"><xbrli:period>
   <xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-03-31</xbrli:endDate>
 </xbrli:period></xbrli:context>
 <in:NatureOfReportStandaloneConsolidated contextRef="OneD">%s</in:NatureOfReportStandaloneConsolidated>
 <in:RevenueFromOperations contextRef="OneD">1000000000</in:RevenueFromOperations>
 <in:ProfitLossForPeriod contextRef="OneD">120000000</in:ProfitLossForPeriod>
</xbrli:xbrl>"""


def test_extract_via_xbrl_standalone(conn):
    _ifiling(conn, "ACME", "31-MAR-2026", "Standalone", "http://x/sa.xml")
    res = xe.extract_via_xbrl(conn, "ACME", "30-Apr-2026",
                              fetch=lambda u: _XBRL % b"Standalone")
    assert res is not None
    assert res.strategy == "xbrl" and res.llm_cost_usd == 0.0
    assert res.period_ending == "2026-03-31"
    assert res.fields["revenue_cr"] == 100.0 and res.fields["pat_cr"] == 12.0
    assert not res.consolidated


def test_extract_via_xbrl_both_scopes_by_nature(conn):
    _ifiling(conn, "ACME", "31-MAR-2026", "Standalone", "http://x/sa.xml")
    _ifiling(conn, "ACME", "31-MAR-2026", "Consolidated", "http://x/con.xml")
    # Slot by the XBRL's own NatureOfReport, regardless of the feed's label.
    def fetch(u):
        return _XBRL % (b"Consolidated" if u.endswith("con.xml") else b"Standalone")
    res = xe.extract_via_xbrl(conn, "ACME", "30-Apr-2026", fetch=fetch)
    assert res.fields["revenue_cr"] == 100.0       # standalone
    assert res.consolidated["revenue_cr"] == 100.0  # consolidated


def test_extract_via_xbrl_none_without_url(conn):
    assert xe.extract_via_xbrl(conn, "NOPE", "30-Apr-2026", fetch=lambda u: b"") is None
