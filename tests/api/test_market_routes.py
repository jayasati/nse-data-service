"""HTTP tests for /api/market/* — regime + sector radar (Phase 2)."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from nse_data.api.deps import get_conn
from nse_data.api.server import create_app


def _seeded_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript("""
        CREATE TABLE market_state (
            as_of TEXT PRIMARY KEY, overall_regime TEXT, vix_state TEXT,
            regime_confidence REAL
        );
        CREATE TABLE sector_state (
            sector_name TEXT, as_of TEXT, rs_rank INTEGER, rs_trend TEXT,
            sector_return_pct REAL, PRIMARY KEY (sector_name, as_of)
        );
        INSERT INTO market_state VALUES ('2026-06-05T10:00:00', 'risk_on', 'normal', 0.8);
        INSERT INTO market_state VALUES ('2026-06-05T09:55:00', 'neutral', 'normal', 0.2);
        -- two snapshots; only the latest should come back, ranked
        INSERT INTO sector_state VALUES ('NIFTY METAL', '2026-06-05T10:00:00', 1, 'improving', 2.0);
        INSERT INTO sector_state VALUES ('NIFTY BANK',  '2026-06-05T10:00:00', 11, 'deteriorating', -0.5);
        INSERT INTO sector_state VALUES ('NIFTY METAL', '2026-06-05T09:55:00', 2, 'flat', 1.0);
    """)
    conn.commit()
    return conn


@pytest.fixture
def client():
    app = create_app()
    conn = _seeded_conn()
    app.dependency_overrides[get_conn] = lambda: conn
    yield TestClient(app)
    conn.close()
    app.dependency_overrides.clear()


def test_regime_returns_latest(client):
    body = client.get("/api/market/regime").json()
    assert body["regime"]["overall_regime"] == "risk_on"   # newest as_of, not 'neutral'
    assert body["regime"]["regime_confidence"] == 0.8


def test_sectors_returns_latest_ranked(client):
    body = client.get("/api/market/sectors").json()
    assert body["as_of"] == "2026-06-05T10:00:00"
    assert body["count"] == 2                               # only the latest snapshot
    assert [s["sector_name"] for s in body["sectors"]] == ["NIFTY METAL", "NIFTY BANK"]
    assert body["sectors"][0]["rs_rank"] == 1               # best first


def test_empty_db_degrades_gracefully():
    app = create_app()
    empty = sqlite3.connect(":memory:", check_same_thread=False)
    app.dependency_overrides[get_conn] = lambda: empty
    c = TestClient(app)
    assert c.get("/api/market/regime").json() == {"regime": None}
    assert c.get("/api/market/sectors").json()["sectors"] == []
    empty.close()
    app.dependency_overrides.clear()
