"""P6 consensus sources — offline (fixtures mirror the real API shapes
captured live on 2026-06-10; no network in tests).

Pinned behaviours: the two parsers' unit handling (Yahoo INR→cr, MC quarterly
labels → quarter-end dates, reported rows excluded), the manual > moneycontrol
> yahoo lookup priority, NII/NIM round-trip via the manual aliases, and the
nightly pass over upcoming reporters with injected fetchers.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from nse_data.events import consensus, consensus_job
from nse_data.events.consensus_sources.moneycontrol import (
    parse_earning_forecast,
    resolve_sc_id,
)
from nse_data.events.consensus_sources.yahoo import parse_quote_summary
from nse_data.events.estimate_scraper import ingest_records
from nse_data.storage.db import apply_migrations

# --- fixtures mirroring the live responses -----------------------------------

YAHOO_FIXTURE = {
    "quoteSummary": {"result": [{"earningsTrend": {"trend": [
        {"period": "0q", "endDate": "2026-06-30",
         "earningsEstimate": {"avg": {"raw": 18.66031}, "earningsCurrency": "INR"},
         "revenueEstimate": {"avg": {"raw": 477853627330}, "revenueCurrency": "INR"}},
        {"period": "+1q", "endDate": "2026-09-30",
         "earningsEstimate": {"avg": {"raw": 19.2}, "earningsCurrency": "INR"},
         "revenueEstimate": {"avg": {"raw": 491000000000}, "revenueCurrency": "INR"}},
        {"period": "0y", "endDate": "2027-03-31",            # annual — ignored
         "earningsEstimate": {"avg": {"raw": 77.0}, "earningsCurrency": "INR"},
         "revenueEstimate": {"avg": {"raw": 2.0e12}, "revenueCurrency": "INR"}},
    ]}}]}
}

MC_FIXTURE = {
    "success": 1,
    "data": {
        "eps": [
            {"date": "Sep 2026", "high": "20", "low": "19", "avg": "19", "actual": ""},
            {"date": "Jun 2026", "high": "20", "low": "18", "avg": "19", "actual": ""},
            {"date": "Mar 2026", "high": "19", "low": "18", "avg": "18", "actual": "21"},
        ],
        "netProfit": [
            {"date": "Jun 2026", "high": "8,243", "low": "7,104", "avg": "7,701", "actual": ""},
            {"date": "Mar 2026", "high": "8053", "low": "7299", "avg": "7567", "actual": "8501"},
        ],
        "revenue": [
            {"date": "Jun 2026", "high": "49,087", "low": "44,800", "avg": "47,785", "actual": ""},
        ],
    },
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c)
    yield c
    c.close()


# --- Yahoo parser -------------------------------------------------------------

def test_yahoo_parses_quarters_in_inr_crore():
    recs = parse_quote_summary(YAHOO_FIXTURE, "INFY")
    assert len(recs) == 2                       # 0q and +1q; annual ignored
    q0 = next(r for r in recs if r["period_ending"] == "2026-06-30")
    assert q0["rev_est_cr"] == pytest.approx(47785.36, abs=0.1)   # ₹477.85B → cr
    assert q0["eps_est"] == pytest.approx(18.66, abs=0.01)


def test_yahoo_skips_non_inr_revenue():
    data = {"quoteSummary": {"result": [{"earningsTrend": {"trend": [
        {"period": "0q", "endDate": "2026-06-30",
         "earningsEstimate": {"avg": {"raw": 0.5}, "earningsCurrency": "USD"},
         "revenueEstimate": {"avg": {"raw": 1.0e9}, "revenueCurrency": "USD"}},
    ]}}]}}
    assert parse_quote_summary(data, "X") == []   # a USD/1e7 misread must never land


def test_yahoo_malformed_payload_is_empty():
    assert parse_quote_summary({}, "INFY") == []
    assert parse_quote_summary({"quoteSummary": {"result": None}}, "INFY") == []


# --- Moneycontrol parser --------------------------------------------------------

def test_mc_parses_future_quarters_only():
    recs = parse_earning_forecast(MC_FIXTURE, "INFY")
    by_period = {r["period_ending"]: r for r in recs}
    assert "2026-03-31" not in by_period         # actual filled = reported, not an estimate
    jun = by_period["2026-06-30"]                # 'Jun 2026' → quarter end
    assert jun["pat_est_cr"] == 7701.0           # comma-grouped string parsed
    assert jun["rev_est_cr"] == 47785.0
    assert jun["eps_est"] == 19.0
    sep = by_period["2026-09-30"]
    assert sep["eps_est"] == 19.0 and sep.get("rev_est_cr") is None


def test_mc_failure_payload_is_empty():
    assert parse_earning_forecast({"success": 0, "data": "x"}, "INFY") == []
    assert parse_earning_forecast({}, "INFY") == []


def test_mc_scid_resolution_matches_nse_symbol():
    class FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            return [
                {"pdt_dis_nm": "Infosys&nbsp;<span>INE009A01021, INFY, 500209</span>", "sc_id": "IT"},
                {"pdt_dis_nm": "Infibeam <span>INE483S01020, INFIBEAM, 539807</span>", "sc_id": "IB"},
            ]

    class FakeClient:
        def get(self, url, params=None):
            return FakeResp()

    assert resolve_sc_id(FakeClient(), "INFY") == "IT"
    assert resolve_sc_id(FakeClient(), "WIPRO") is None


# --- source priority + BFSI fields ----------------------------------------------

def test_manual_outranks_live_sources(conn):
    now = int(time.time())
    consensus.upsert_estimate(conn, symbol="SBIN", period_ending="2026-06-30",
                              pat_est_cr=19000.0, source="yahoo", as_of=now + 100)
    consensus.upsert_estimate(conn, symbol="SBIN", period_ending="2026-06-30",
                              pat_est_cr=18800.0, source="moneycontrol", as_of=now + 50)
    consensus.upsert_estimate(conn, symbol="SBIN", period_ending="2026-06-30",
                              pat_est_cr=18500.0, nii_est_cr=44000.0, nim_est_pct=3.0,
                              source="manual", as_of=now)   # oldest, still wins
    est = consensus.nearest_estimate(conn, "SBIN", "2026-06-30")
    assert est["source"] == "manual"
    assert est["pat_est_cr"] == 18500.0
    assert est["nii_est_cr"] == 44000.0 and est["nim_est_pct"] == 3.0

    rows = consensus.estimates_by_source(conn, "SBIN", "2026-06-30")
    assert [r["source"] for r in rows] == ["manual", "moneycontrol", "yahoo"]


def test_nearest_estimate_tolerance_prefers_closer_period(conn):
    consensus.upsert_estimate(conn, symbol="TCS", period_ending="2026-06-30",
                              pat_est_cr=12500.0, source="manual")
    est = consensus.nearest_estimate(conn, "TCS", "2026-07-05")   # 5 days off
    assert est is not None and est["pat_est_cr"] == 12500.0
    assert consensus.nearest_estimate(conn, "TCS", "2026-09-30") is None


def test_manual_csv_aliases_carry_nii_nim(conn):
    n = ingest_records(conn, [
        {"symbol": "SBIN", "quarter_end": "2026-06-30", "pat_est": "18,500",
         "nii_est": "44,000", "nim": "3.0"},
    ], source="manual")
    assert n == 1
    est = consensus.nearest_estimate(conn, "SBIN", "2026-06-30")
    assert est["nii_est_cr"] == 44000.0 and est["nim_est_pct"] == 3.0


# --- the nightly pass --------------------------------------------------------------

def test_run_consensus_pass_over_upcoming_reporters(conn, monkeypatch):
    conn.execute(
        "INSERT INTO pending_events (symbol, event_type, expected_date, status, created_at) "
        "VALUES ('INFY', 'result', date('now', '+3 days'), 'upcoming', 0)"
    )
    conn.execute(
        "INSERT INTO pending_events (symbol, event_type, expected_date, status, created_at) "
        "VALUES ('OLDCO', 'result', date('now', '-30 days'), 'upcoming', 0)"
    )
    conn.commit()
    assert consensus_job.upcoming_symbols(conn) == ["INFY"]

    fake_records = [{"symbol": "INFY", "period_ending": "2026-06-30",
                     "pat_est_cr": 7701.0, "rev_est_cr": 47785.0}]
    import nse_data.events.consensus_sources as cs
    monkeypatch.setattr(cs, "make_news_fetcher", lambda c, **k: lambda s: [
        {"symbol": "INFY", "period_ending": "2026-06-30", "nii_est_cr": None,
         "pat_est_cr": 7650.0}])
    monkeypatch.setattr(cs, "make_moneycontrol_fetcher", lambda **k: lambda s: fake_records)
    monkeypatch.setattr(cs, "make_yahoo_fetcher", lambda **k: lambda s: [
        {"symbol": "INFY", "period_ending": "2026-06-30", "eps_est": 18.66,
         "rev_est_cr": 47785.4}])

    report = consensus_job.run_consensus_pass(conn)
    assert report == {"symbols": 1, "news": 1, "moneycontrol": 1, "yahoo": 1}

    # field-wise merge in rank order: PAT from news (outranks MC), revenue from
    # MC, EPS only Yahoo carries — all in one lookup.
    est = consensus.nearest_estimate(conn, "INFY", "2026-06-30")
    assert est["source"] == "news+moneycontrol+yahoo"
    assert est["pat_est_cr"] == 7650.0
    assert est["rev_est_cr"] == 47785.0
    assert est["eps_est"] == 18.66
    # cross-validation view: all sources stored, revenue within rounding
    rows = consensus.estimates_by_source(conn, "INFY", "2026-06-30")
    assert len(rows) == 3
    assert rows[1]["rev_est_cr"] == pytest.approx(rows[2]["rev_est_cr"], rel=0.01)


def test_run_consensus_pass_no_upcoming_is_noop(conn):
    assert consensus_job.run_consensus_pass(conn) == {"symbols": 0}