"""
Acceptance tests for the ShareholdingPattern collector.

ReferenceCollector (diff_upsert, key=symbol) over the share-holdings master.
plan() pulls equities + sme; tests narrow `indexes` to one segment so the two
same-path requests don't feed diff_upsert duplicate keys. Fixtures are live
captures (2 equity rows, 1 sme row).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.shareholding import ShareholdingPattern

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIX_DIR = Path(__file__).parent.parent / "fixtures"
EQ_PATH = FIX_DIR / "shareholding_equities.json"
SME_PATH = FIX_DIR / "shareholding_sme.json"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"
ENDPOINT = "/api/corporate-share-holdings-master"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


@pytest.fixture
def eq_data():
    return json.loads(EQ_PATH.read_text())


def _eq_collector():
    """Collector narrowed to the equities segment (single request)."""
    c = ShareholdingPattern()
    c.indexes = ("equities",)
    return c


# ============================================================================
# plan()
# ============================================================================

def test_plan_pulls_both_segments():
    reqs = ShareholdingPattern().plan()
    assert {r.params["index"] for r in reqs} == {"equities", "sme"}


# ============================================================================
# normalize()
# ============================================================================

def test_normalize_maps_ownership_split(eq_data):
    rows = ShareholdingPattern().normalize(
        eq_data, Request(path_or_url="x", meta={"segment": "equities"})
    )
    micron = next(r for r in rows if r["symbol"] == "20MICRONS")
    assert micron["segment"] == "equities"
    assert micron["company_name"] == "20 Microns Limited"
    assert micron["promoter_pct"] == 45.04
    assert micron["public_pct"] == 54.96
    assert micron["employee_trust_pct"] == 0.0
    assert micron["qe_date"] == "31-MAR-2026"
    assert micron["record_id"] == "208915"
    assert micron["xbrl_url"].endswith("_WEB.xml")


def test_industry_dash_becomes_none(eq_data):
    micron = ShareholdingPattern().normalize(eq_data, Request(path_or_url="x"))[0]
    assert micron["industry"] is None   # NSE sent "-"


def test_sme_segment_with_isin():
    sme = json.loads(SME_PATH.read_text())
    rows = ShareholdingPattern().normalize(
        sme, Request(path_or_url="x", meta={"segment": "sme"})
    )
    vilin = rows[0]
    assert vilin["segment"] == "sme"
    assert vilin["isin"] == "INE0L4V01013"
    assert vilin["promoter_pct"] == 56.65


def test_dict_wrapped_and_empty_inputs():
    c = ShareholdingPattern()
    assert c.normalize({"data": []}, Request(path_or_url="x")) == []
    assert c.normalize([], Request(path_or_url="x")) == []
    assert c.normalize(None, Request(path_or_url="x")) == []


def test_drops_empty_symbol():
    payload = [{"symbol": "", "name": "X"}, {"name": "Y"}]
    assert ShareholdingPattern().normalize(payload, Request(path_or_url="x")) == []


# ============================================================================
# Integration — diff semantics
# ============================================================================

def test_run_inserts(db, eq_data):
    session = FakeSession(json_fixtures={ENDPOINT: eq_data})
    report = _eq_collector().run(session, db)
    assert report.persist.inserted == 2
    n = db.execute("SELECT COUNT(*) FROM raw_shareholding_pattern").fetchone()[0]
    assert n == 2


def test_promoter_change_is_an_update(db, eq_data):
    """A promoter_pct move next quarter shows as 'updated', not a new row."""
    _eq_collector().run(FakeSession(json_fixtures={ENDPOINT: eq_data}), db)

    bumped = json.loads(json.dumps(eq_data))
    bumped[0]["pr_and_prgrp"] = "50.00"
    bumped[0]["public_val"] = "50.00"
    report = _eq_collector().run(FakeSession(json_fixtures={ENDPOINT: bumped}), db)

    assert report.persist.updated == 1
    assert report.persist.unchanged == 1
    pct = db.execute(
        "SELECT promoter_pct FROM raw_shareholding_pattern WHERE symbol='20MICRONS'"
    ).fetchone()[0]
    assert pct == 50.0


def test_delisted_symbol_removed(db, eq_data):
    _eq_collector().run(FakeSession(json_fixtures={ENDPOINT: eq_data}), db)
    shrunk = [r for r in eq_data if r["symbol"] == "20MICRONS"]
    report = _eq_collector().run(FakeSession(json_fixtures={ENDPOINT: shrunk}), db)
    assert report.persist.removed == 1
    symbols = {r[0] for r in db.execute("SELECT symbol FROM raw_shareholding_pattern")}
    assert symbols == {"20MICRONS"}
