"""
Acceptance tests for the IntegratedFilings collector.

The collector pulls two filing types (Financials + Governance) from one
endpoint and dedups by filing_type|seq_id into raw_integrated_filings.
Fixtures are 3-row live captures per type.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.integrated_filings import IntegratedFilings

from ..conftest import FakeSession   # type: ignore[import-not-found]


FIX_DIR = Path(__file__).parent.parent / "fixtures"
FIN_PATH = FIX_DIR / "integrated_filings_financials.json"
GOV_PATH = FIX_DIR / "integrated_filings_governance.json"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"
ENDPOINT = "/api/integrated-filing-results"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


@pytest.fixture
def fin_data():
    return json.loads(FIN_PATH.read_text())


@pytest.fixture
def gov_data():
    return json.loads(GOV_PATH.read_text())


def _norm(data):
    return IntegratedFilings().normalize(data, Request(path_or_url="x"))


# ============================================================================
# plan()
# ============================================================================

def test_plan_issues_both_types():
    reqs = IntegratedFilings().plan()
    assert len(reqs) == 2
    types = {r.params["type"] for r in reqs}
    assert types == {"Integrated Filing- Financials", "Integrated Filing- Governance"}
    assert all(r.params["size"] == "500" for r in reqs)


# ============================================================================
# normalize()
# ============================================================================

def test_normalize_financials(fin_data):
    rows = _norm(fin_data)
    assert len(rows) == 3
    clsel = next(r for r in rows if r["symbol"] == "CLSEL")
    assert clsel["seq_id"] == "161543"
    assert clsel["filing_type"] == "Integrated Filing- Financials"
    assert clsel["type_sub"] == "Original"
    assert clsel["qe_date"] == "31-MAR-2026"
    assert clsel["audited"] == "Audited"
    assert clsel["consolidated"] == "Standalone"
    assert clsel["ixbrl_url"].endswith("_iXBRL_WEB.html")


def test_placeholder_pdf_url_becomes_none(fin_data):
    """CLSEL's pdf_attach is '.../corporate/null' — must store NULL, not the junk."""
    clsel = next(r for r in _norm(fin_data) if r["symbol"] == "CLSEL")
    assert clsel["pdf_url"] is None


def test_normalize_governance(gov_data):
    rows = _norm(gov_data)
    assert len(rows) == 3
    assert all(r["filing_type"] == "Integrated Filing- Governance" for r in rows)
    assert all(r["type_sub"] == "Revision" for r in rows)


def test_skips_rows_without_seq_or_type():
    payload = {"data": [
        {"seq_Id": None, "type": "Integrated Filing- Financials"},
        {"seq_Id": "1", "type": None},
        {"symbol": "X"},
    ]}
    assert _norm(payload) == []


def test_handles_empty_and_malformed():
    c = IntegratedFilings()
    assert c.normalize({"data": []}, Request(path_or_url="x")) == []
    assert c.normalize({"data": None, "msg": "no data found"}, Request(path_or_url="x")) == []
    assert c.normalize([], Request(path_or_url="x")) == []
    assert c.normalize(None, Request(path_or_url="x")) == []


# ============================================================================
# fingerprint — type-prefixed, collision-safe across types
# ============================================================================

def test_fingerprint_type_prefixed(fin_data):
    row = _norm(fin_data)[0]
    expected = hashlib.sha256(
        f"{row['filing_type']}|{row['seq_id']}".encode()
    ).hexdigest()[:16]
    assert IntegratedFilings().fingerprint(row) == expected


def test_no_cross_type_collision(fin_data, gov_data):
    c = IntegratedFilings()
    fps = {c.fingerprint(r) for r in _norm(fin_data) + _norm(gov_data)}
    assert len(fps) == 6   # 3 financials + 3 governance, all distinct


# ============================================================================
# Integration — run()/persist/dedup
#
# plan() issues two same-path requests; the FakeSession returns the registered
# fixture for both, so the collector sees that payload twice in one run. The
# in-batch duplicates dedup on the fingerprint PK, proving idempotency.
# ============================================================================

def test_run_inserts_and_dedups_within_batch(fin_data, db):
    session = FakeSession(json_fixtures={ENDPOINT: fin_data})
    report = IntegratedFilings().run(session, db)
    # 2 requests × 3 rows seen, but only 3 unique fingerprints.
    assert report.rows_seen == 6
    assert report.persist.inserted == 3
    assert report.persist.deduped == 3
    n = db.execute("SELECT COUNT(*) FROM raw_integrated_filings").fetchone()[0]
    assert n == 3


def test_rerun_is_idempotent(fin_data, db):
    IntegratedFilings().run(FakeSession(json_fixtures={ENDPOINT: fin_data}), db)
    r2 = IntegratedFilings().run(FakeSession(json_fixtures={ENDPOINT: fin_data}), db)
    assert r2.persist.inserted == 0
    assert db.execute("SELECT COUNT(*) FROM raw_integrated_filings").fetchone()[0] == 3
