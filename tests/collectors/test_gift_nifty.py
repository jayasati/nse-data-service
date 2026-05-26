"""
Acceptance tests for the GiftNifty collector (NSE IX, external).

normalize() against a real captured /api/nifty-market-rate payload; run() with
fetch() overridden (no network). Snapshot semantics: each poll appends a tick.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.gift_nifty import GiftNifty


FIXTURE = Path(__file__).parent.parent / "fixtures" / "gift_nifty.json"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


@pytest.fixture
def payload():
    return json.loads(FIXTURE.read_text())


# ---- normalize ----

def test_normalize_parses_and_strips_comma(payload):
    row = GiftNifty().normalize(payload, Request(path_or_url="x"))[0]
    assert row["index_name"] == "Nifty 50"
    assert row["curr_value"] == 23913.70        # "23,913.70" comma stripped
    assert row["open_value"] == pytest.approx(24004.1)
    assert row["close_value"] == 23913.7
    assert row["change"] == -118.0
    assert row["pct_change"] == -0.49
    assert row["nse_timestamp"] == "26-May-2026 15:30"


def test_normalize_handles_dict_and_garbage():
    c = GiftNifty()
    # single dict (not list) tolerated
    assert c.normalize({"OI_INDEX_NAME": "X", "CURRVALUE": "1,000"},
                       Request(path_or_url="x"))[0]["curr_value"] == 1000.0
    assert c.normalize([], Request(path_or_url="x")) == []
    assert c.normalize(None, Request(path_or_url="x")) == []
    assert c.normalize([{"CURRVALUE": "1"}], Request(path_or_url="x")) == []  # no index name


# ---- run() with fetch override (no network) ----

def _collector(payload=None, error=None):
    class _T(GiftNifty):
        def fetch(self, client):
            if error:
                raise error
            return payload
    return _T()


def test_run_inserts_tick(db, payload):
    report = _collector(payload).run(session=None, db=db)
    assert report.succeeded == 1
    assert report.persist.inserted == 1
    assert db.execute("SELECT COUNT(*) FROM raw_gift_nifty").fetchone()[0] == 1


def test_run_handles_fetch_error(db):
    report = _collector(error=RuntimeError("nseix 503")).run(session=None, db=db)
    assert report.failed == 1
    assert report.persist.inserted == 0
    assert db.execute("SELECT COUNT(*) FROM raw_gift_nifty").fetchone()[0] == 0


def test_successive_polls_accumulate(db, payload, monkeypatch):
    fake = [1_700_000_000]
    monkeypatch.setattr(time, "time", lambda: fake[0])
    c = _collector(payload)
    c.run(session=None, db=db)
    fake[0] += 30   # 30s later
    c.run(session=None, db=db)
    total = db.execute("SELECT COUNT(*) FROM raw_gift_nifty").fetchone()[0]
    assert total == 2   # two distinct as_of ticks
