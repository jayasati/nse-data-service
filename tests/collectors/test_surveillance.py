"""
Phase 5 acceptance tests — three surveillance collectors + the unified blacklist.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.surveillance import (
    AsmLongTermSurveillance,
    AsmShortTermSurveillance,
    GsmSurveillance,
)

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


@pytest.fixture
def gsm_data():
    return json.loads((FIXTURE_DIR / "surveillance_gsm.json").read_text())


@pytest.fixture
def asm_data():
    return json.loads((FIXTURE_DIR / "surveillance_asm.json").read_text())


@pytest.fixture
def session(gsm_data, asm_data):
    return FakeSession(json_fixtures={
        "/api/reportGSM": gsm_data,
        "/api/reportASM": asm_data,
    })


# ============================================================================
# Unit — normalize()
# ============================================================================

def test_gsm_normalize_drops_malformed_rows(gsm_data):
    rows = GsmSurveillance().normalize(gsm_data, Request(path_or_url="x"))
    # 4 entries in fixture, last has empty symbol -> dropped
    assert len(rows) == 3
    assert all(r["symbol"] for r in rows)


def test_gsm_normalize_maps_fields(gsm_data):
    rows = GsmSurveillance().normalize(gsm_data, Request(path_or_url="x"))
    ankit = next(r for r in rows if r["symbol"] == "ANKITMETAL")
    assert ankit["company_name"] == "Ankit Metal & Power Limited"
    assert ankit["isin"] == "INE106I01010"
    assert ankit["stage"] == "LXII"
    assert ankit["surv_code"].startswith("IBC")
    assert "08:06:03" in ankit["as_on"]


def test_asm_lt_only_returns_longterm_block(asm_data):
    rows = AsmLongTermSurveillance().normalize(asm_data, Request(path_or_url="x"))
    symbols = {r["symbol"] for r in rows}
    assert "21STCENMGM" in symbols
    assert "ACE" in symbols
    assert "63MOONS" not in symbols   # belongs to shortterm


def test_asm_st_only_returns_shortterm_block(asm_data):
    rows = AsmShortTermSurveillance().normalize(asm_data, Request(path_or_url="x"))
    symbols = {r["symbol"] for r in rows}
    assert "63MOONS" in symbols
    assert "21STCENMGM" not in symbols


def test_asm_normalize_maps_fields(asm_data):
    rows = AsmLongTermSurveillance().normalize(asm_data, Request(path_or_url="x"))
    twentyfirst = next(r for r in rows if r["symbol"] == "21STCENMGM")
    assert twentyfirst["stage"] == "Stage I"
    assert twentyfirst["surv_code"].startswith("LTASM")


def test_normalize_handles_empty_response():
    assert GsmSurveillance().normalize([], Request(path_or_url="x")) == []
    assert GsmSurveillance().normalize({}, Request(path_or_url="x")) == []
    assert AsmLongTermSurveillance().normalize({}, Request(path_or_url="x")) == []
    assert AsmLongTermSurveillance().normalize(
        {"longterm": {}}, Request(path_or_url="x")
    ) == []


# ============================================================================
# Integration — diff_upsert semantics
# ============================================================================

def test_first_run_inserts_all(session, db):
    """All three collectors land their rows on the first run."""
    gsm_report = GsmSurveillance().run(session, db)
    asm_lt_report = AsmLongTermSurveillance().run(session, db)
    asm_st_report = AsmShortTermSurveillance().run(session, db)

    assert gsm_report.persist.inserted == 3
    assert asm_lt_report.persist.inserted == 2
    assert asm_st_report.persist.inserted == 1


def test_rerun_with_same_data_is_unchanged(session, db):
    GsmSurveillance().run(session, db)
    report = GsmSurveillance().run(session, db)
    assert report.persist.inserted == 0
    assert report.persist.unchanged == 3
    assert report.persist.removed == 0


def test_symbol_disappearing_is_removed(gsm_data, db):
    # First run: 3 GSM symbols land
    s1 = FakeSession(json_fixtures={"/api/reportGSM": gsm_data})
    GsmSurveillance().run(s1, db)

    # Second run: NSE drops ANSALAPI (it escaped GSM)
    fresh = [r for r in gsm_data if r.get("symbol") != "ANSALAPI"]
    s2 = FakeSession(json_fixtures={"/api/reportGSM": fresh})
    report = GsmSurveillance().run(s2, db)

    assert report.persist.removed == 1
    in_db = db.execute(
        "SELECT COUNT(*) FROM raw_surveillance_gsm WHERE symbol='ANSALAPI'"
    ).fetchone()[0]
    assert in_db == 0


def test_stage_bump_is_updated(gsm_data, db):
    s1 = FakeSession(json_fixtures={"/api/reportGSM": gsm_data})
    GsmSurveillance().run(s1, db)

    # ANKITMETAL gets bumped from LXII to LXIII
    mutated = json.loads(json.dumps(gsm_data))   # deep copy
    for r in mutated:
        if r.get("symbol") == "ANKITMETAL":
            r["gsmStage"] = "LXIII"
            r["survCode"] = "IBC - Receipt & GSM 0 (63)"
    s2 = FakeSession(json_fixtures={"/api/reportGSM": mutated})
    report = GsmSurveillance().run(s2, db)

    assert report.persist.updated == 1
    new_stage = db.execute(
        "SELECT stage FROM raw_surveillance_gsm WHERE symbol='ANKITMETAL'"
    ).fetchone()[0]
    assert new_stage == "LXIII"


# ============================================================================
# Integration — unified blacklist view
# ============================================================================

def test_blacklist_unions_all_three_feeds(session, db):
    GsmSurveillance().run(session, db)
    AsmLongTermSurveillance().run(session, db)
    AsmShortTermSurveillance().run(session, db)

    total = db.execute("SELECT COUNT(*) FROM blacklist").fetchone()[0]
    assert total == 3 + 2 + 1   # GSM + ASM-LT + ASM-ST

    feeds = {row[0] for row in db.execute("SELECT DISTINCT feed FROM blacklist")}
    assert feeds == {"GSM", "ASM-LT", "ASM-ST"}


def test_blacklist_filter_query_returns_reason(session, db):
    """Layer 6's hard-filter pattern: 'is this symbol blacklisted, and why?'"""
    GsmSurveillance().run(session, db)

    row = db.execute(
        "SELECT feed, stage, reason FROM blacklist WHERE symbol = 'ANKITMETAL'"
    ).fetchone()
    assert row is not None
    assert row[0] == "GSM"
    assert row[1] == "LXII"
    assert row[2].startswith("IBC")