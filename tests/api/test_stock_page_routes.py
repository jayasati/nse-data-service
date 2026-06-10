"""/api/stocks/{symbol}/* cockpit tabs — seeded-DB happy paths + the
empty-DB guarantee (every section renders empty, never 500s)."""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from nse_data.api.deps import get_conn
from nse_data.api.server import create_app
from nse_data.storage.db import apply_migrations

TABS = ("overview", "results", "events", "filings", "activity", "flow")


def _seeded() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", check_same_thread=False)
    apply_migrations(c)
    growth = json.dumps({"yoy_pat_pct": 5.6, "yoy_ppop_pct": -11.45,
                         "yoy_provisions_pct": -36.6, "yoy_revenue_pct": 6.3})
    narrative = json.dumps({"dividend": 17.35})
    c.executescript(f"""
        INSERT INTO extracted_financials (symbol, period_ending, scope, revenue_cr,
            pat_cr, net_interest_income_cr, operating_profit_cr, provisions_cr,
            profit_on_sale_of_investments_cr, growth_json, narrative_json, extracted_at)
        VALUES ('SBIN', '2026-03-31', 'standalone', 117996, 19684, 44380, 27704,
                2700, -1471, '{growth}', '{narrative}', 0);
        INSERT INTO consensus_estimates (symbol, period_ending, pat_est_cr,
            nii_est_cr, source, as_of)
        VALUES ('SBIN', '2026-06-30', 18500, 44000, 'manual', 0);
        INSERT INTO pending_events (symbol, event_type, expected_date, status,
            confidence, created_at)
        VALUES ('SBIN', 'result', date('now', '+20 days'), 'upcoming', 'confirmed', 0);
        INSERT INTO raw_announcements (fingerprint, segment, symbol, subject,
            broadcast_dt, pdf_status, created_at)
        VALUES ('f1', 'EQ', 'SBIN', 'Financial Results', '08-May-2026 14:01:38',
                'text_extracted', 5);
        INSERT INTO signals (symbol, signal_type, detected_at, price, confidence,
            direction, dispatched)
        VALUES ('SBIN', 'result_quality_low', '2026-05-08T14:43:00', 779.0, 0.82,
                'short', 1);
        INSERT INTO raw_large_deals (fingerprint, deal_type, deal_date, symbol,
            client_name, buy_sell, quantity, weighted_avg_price, created_at)
        VALUES ('d1', 'bulk', '2026-05-09', 'SBIN', 'BIG FUND', 'SELL', 9000000,
                760.5, 0);
    """)
    c.commit()
    return c


@pytest.fixture
def client():
    app = create_app()
    conn = _seeded()
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    conn.close()
    app.dependency_overrides.clear()


@pytest.fixture
def empty_client():
    app = create_app()
    conn = sqlite3.connect(":memory:", check_same_thread=False)  # NO tables at all
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    conn.close()
    app.dependency_overrides.clear()


def test_overview_carries_verdict_and_event(client):
    o = client.get("/api/stocks/SBIN/overview").json()
    v = o["result_verdict"]
    assert v["label"] == "low" and v["direction"] == "short"     # the SBI hidden miss
    assert "low_quality_beat" in v["flags"]
    assert o["next_event"]["event_type"] == "result"
    assert o["consensus_sources"] == ["manual"]


def test_results_tab_quarters_verdict_estimates(client):
    r = client.get("/api/stocks/SBIN/results").json()
    assert r["quarters"][0]["ppop_cr"] == 27704
    assert r["quarters"][0]["yoy_ppop_pct"] == -11.45
    assert r["verdict"]["narrative"]["dividend"] == 17.35
    est = r["estimates"][0]
    assert est["period_ending"] == "2026-06-30"
    assert est["rows"][0]["nii_est_cr"] == 44000


def test_other_tabs_return_seeded_rows(client):
    assert client.get("/api/stocks/SBIN/events").json()["pending"]
    assert client.get("/api/stocks/SBIN/filings").json()["announcements"][0][
        "subject"] == "Financial Results"
    a = client.get("/api/stocks/SBIN/activity").json()
    assert a["signals"][0]["signal_type"] == "result_quality_low"
    f = client.get("/api/stocks/SBIN/flow").json()
    assert f["large_deals"][0]["client_name"] == "BIG FUND"


def test_unknown_symbol_is_empty_not_error(client):
    for tab in TABS:
        r = client.get(f"/api/stocks/ZZNOTREAL/{tab}")
        assert r.status_code == 200, tab


def test_tableless_db_never_500s(empty_client):
    """A fresh deploy with zero tables must render an empty cockpit, not crash."""
    for tab in TABS:
        r = empty_client.get(f"/api/stocks/SBIN/{tab}")
        assert r.status_code == 200, tab