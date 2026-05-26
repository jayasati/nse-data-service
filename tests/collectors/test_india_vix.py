"""
India VIX acceptance tests — derived 1σ / 2σ expected-range envelopes.

Reuses the shared indices_allindices.json fixture, which carries both the
INDIA VIX and NIFTY 50 rows the collector needs.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.india_vix import TRADING_DAYS, IndiaVix

from ..conftest import FakeSession  # type: ignore[import-not-found]

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


def _load(name: str):
    return json.loads((FIXTURE_DIR / name).read_text())


def test_normalize_derives_envelopes():
    data = _load("indices_allindices.json")
    rows = IndiaVix().normalize(data, Request(path_or_url="/api/allIndices"))
    assert len(rows) == 1
    row = rows[0]

    # Fixture values: VIX last = 18.57, NIFTY 50 last = 23676.25
    assert row["vix"] == 18.57
    assert row["nifty_spot"] == 23676.25

    # 1σ daily % = VIX / sqrt(252)
    expected_pct = 18.57 / math.sqrt(TRADING_DAYS)
    assert row["expected_move_pct"] == pytest.approx(expected_pct, abs=1e-4)

    # Envelope is symmetric around spot, and 2σ is exactly twice the 1σ offset.
    move_pts = 23676.25 * expected_pct / 100
    assert row["sigma1_upper"] == pytest.approx(23676.25 + move_pts, abs=0.01)
    assert row["sigma1_lower"] == pytest.approx(23676.25 - move_pts, abs=0.01)
    assert row["sigma2_upper"] == pytest.approx(23676.25 + 2 * move_pts, abs=0.01)
    assert row["sigma2_lower"] == pytest.approx(23676.25 - 2 * move_pts, abs=0.01)

    # Spot sits at the midpoint of every band.
    assert (row["sigma1_upper"] + row["sigma1_lower"]) / 2 == pytest.approx(23676.25, abs=0.01)
    assert row["sigma2_upper"] - row["sigma1_upper"] == pytest.approx(move_pts, abs=0.02)


def test_no_vix_row_yields_nothing():
    rows = IndiaVix().normalize({"data": [{"index": "NIFTY 50", "last": 100}]},
                                Request(path_or_url="x"))
    assert rows == []


def test_missing_nifty_spot_keeps_vix_pct_null_bands():
    data = {"data": [{"index": "INDIA VIX", "indexSymbol": "INDIA VIX",
                      "last": 20.0, "open": 19.5}]}
    rows = IndiaVix().normalize(data, Request(path_or_url="x"))
    assert len(rows) == 1
    row = rows[0]
    assert row["vix"] == 20.0
    assert row["nifty_spot"] is None
    # The percentage is independent of spot and is still computed.
    assert row["expected_move_pct"] == pytest.approx(20.0 / math.sqrt(TRADING_DAYS), abs=1e-4)
    # Point envelopes need the anchor — left NULL.
    assert row["sigma1_upper"] is None and row["sigma2_lower"] is None


def test_runs_end_to_end(db):
    data = _load("indices_allindices.json")
    session = FakeSession(json_fixtures={"/api/allIndices": data})
    report = IndiaVix().run(session, db)

    assert report.succeeded == 1
    assert report.persist.inserted == 1

    row = db.execute(
        "SELECT vix, nifty_spot, sigma1_upper, sigma2_lower FROM raw_india_vix"
    ).fetchone()
    assert row[0] == 18.57
    assert row[1] == 23676.25
    assert row[2] > 23676.25   # upper band above spot
    assert row[3] < 23676.25   # lower 2σ band below spot


def test_reruns_accumulate_intraday_series(db, monkeypatch):
    data = _load("indices_allindices.json")
    session = FakeSession(json_fixtures={"/api/allIndices": data})

    fake_now = [1_700_000_000]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    IndiaVix().run(session, db)
    fake_now[0] += 300  # next 5-min poll
    IndiaVix().run(session, db)

    assert db.execute("SELECT COUNT(*) FROM raw_india_vix").fetchone()[0] == 2
