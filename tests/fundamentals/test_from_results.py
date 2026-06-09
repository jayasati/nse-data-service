"""E1 tests: persist extracted financials + YoY/QoQ growth (no API/PDF needed)."""
from __future__ import annotations

import pytest

from nse_data.fundamentals import from_results as fr
from nse_data.parsers.financial_extractor import ExtractionResult
from nse_data.parsers.state import State
from nse_data.storage import db as dbmod


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.open_db(str(tmp_path / "t.db"))
    dbmod.apply_migrations(c, migrations_dir="migrations")
    yield c
    c.close()


def _store(conn, symbol, period, *, rev, pat, scope="standalone", fp=None):
    fr.persist_extraction(
        conn, symbol=symbol, period_ending=period, scope=scope,
        fields={"revenue_cr": rev, "pat_cr": pat, "total_income_cr": rev + 5},
        units_phrase="INR crore", confidence=1.0, strategy="vision",
        source_fingerprint=fp, broadcast_dt=None, now=1700000000,
    )


# --------------------------------------------------------------------------- #
# subject detection
# --------------------------------------------------------------------------- #

def test_is_result_subject():
    assert fr.is_result_subject("Financial Results for Q4")
    assert fr.is_result_subject("Outcome of Board Meeting")
    assert fr.is_result_subject("Un-audited Financial Results")
    assert not fr.is_result_subject("Acquisition of subsidiary")
    assert not fr.is_result_subject(None)


# --------------------------------------------------------------------------- #
# growth math
# --------------------------------------------------------------------------- #

def test_quarter_growth_yoy_and_qoq(conn):
    # Q4-FY26 vs Q4-FY25 (YoY) and Q3-FY26 (QoQ)
    _store(conn, "ACME", "2025-03-31", rev=100, pat=10)   # year-ago quarter
    _store(conn, "ACME", "2025-12-31", rev=120, pat=14)   # prior quarter
    _store(conn, "ACME", "2026-03-31", rev=130, pat=20)   # current
    g = fr.quarter_growth(conn, "ACME", "2026-03-31")
    assert g["yoy_revenue_pct"] == 30.0     # 100 -> 130
    assert g["yoy_pat_pct"] == 100.0        # 10 -> 20
    assert g["qoq_revenue_pct"] == pytest.approx(8.33, abs=0.01)   # 120 -> 130
    assert g["qoq_pat_pct"] == pytest.approx(42.86, abs=0.01)      # 14 -> 20


def test_quarter_growth_sign_aware_pat_turnaround(conn):
    # loss last year -> profit this year: prior is negative, so % uses abs(prior)
    _store(conn, "TURN", "2025-03-31", rev=80, pat=-10)
    _store(conn, "TURN", "2026-03-31", rev=100, pat=5)
    g = fr.quarter_growth(conn, "TURN", "2026-03-31")
    assert g["yoy_revenue_pct"] == 25.0
    assert g["yoy_pat_pct"] == 150.0        # (5 - (-10)) / abs(-10) = 1.5 -> 150%


def test_quarter_growth_empty_when_no_history(conn):
    _store(conn, "SOLO", "2026-03-31", rev=100, pat=10)
    assert fr.quarter_growth(conn, "SOLO", "2026-03-31") == {}


def test_quarter_growth_scope_isolation(conn):
    _store(conn, "DUO", "2025-03-31", rev=100, pat=10, scope="consolidated")
    _store(conn, "DUO", "2026-03-31", rev=200, pat=20, scope="consolidated")
    # standalone has no rows -> empty; consolidated computes
    assert fr.quarter_growth(conn, "DUO", "2026-03-31", scope="standalone") == {}
    g = fr.quarter_growth(conn, "DUO", "2026-03-31", scope="consolidated")
    assert g["yoy_revenue_pct"] == 100.0


# --------------------------------------------------------------------------- #
# extract_and_store + run_extract_pass (extractor faked)
# --------------------------------------------------------------------------- #

def _insert_announcement(conn, fp, symbol, subject):
    conn.execute(
        "INSERT INTO raw_announcements "
        "(fingerprint, segment, symbol, subject, broadcast_dt, pdf_status, pdf_path, created_at) "
        "VALUES (?, 'equities', ?, ?, '01-May-2026 10:00:00', ?, ?, 1700000000)",
        (fp, symbol, subject, State.TEXT_EXTRACTED, f"/tmp/{fp}.pdf"),
    )
    conn.commit()


def test_extract_and_store_persists_both_scopes(conn, monkeypatch):
    _insert_announcement(conn, "fp1", "ACME", "Financial Results")
    fake = ExtractionResult(
        fields={"revenue_cr": 130.0, "pat_cr": 20.0},
        consolidated={"revenue_cr": 150.0, "pat_cr": 25.0},
        confidence=1.0, strategy="vision",
        units_phrase="INR crore", period_ending="2026-03-31",
    )
    monkeypatch.setattr("nse_data.parsers.financial_extractor.extract",
                        lambda *a, **k: fake)
    r = fr.extract_and_store(conn, fingerprint="fp1", symbol="ACME",
                             subject="Financial Results", broadcast_dt=None,
                             pdf_path="/tmp/fp1.pdf", use_llm=True, now=1700000000)
    assert r["stored"] == 2
    rows = conn.execute(
        "SELECT scope, revenue_cr FROM extracted_financials WHERE symbol='ACME' ORDER BY scope"
    ).fetchall()
    assert rows == [("consolidated", 150.0), ("standalone", 130.0)]
    # announcement advanced to a terminal extracted state
    st = conn.execute("SELECT pdf_status FROM raw_announcements WHERE fingerprint='fp1'").fetchone()[0]
    assert st == State.EXTRACTED_VIA_VISION


class _StubScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, fn, **kw):
        self.jobs.append(kw.get("id"))


def test_register_extract_runner_schedules():
    sched = _StubScheduler()
    job_id = fr.register_extract_runner(sched, ":memory:")
    assert job_id == "extract_financials"
    assert "extract_financials" in sched.jobs


def test_register_intraday_extract_runner_schedules():
    sched = _StubScheduler()
    job_id = fr.register_intraday_extract_runner(sched, ":memory:")
    assert job_id == "extract_financials_intraday"
    assert "extract_financials_intraday" in sched.jobs


def test_register_fast_result_lane_schedules():
    sched = _StubScheduler()
    job_id = fr.register_fast_result_lane(sched, ":memory:", session=object())
    assert job_id == "fast_result_lane"
    assert "fast_result_lane" in sched.jobs


def test_run_extract_pass_filters_and_dedups(conn, monkeypatch):
    _insert_announcement(conn, "fpR", "ACME", "Financial Results")
    _insert_announcement(conn, "fpX", "NOPE", "Acquisition of stake")   # not a result
    fake = ExtractionResult(
        fields={"revenue_cr": 100.0, "pat_cr": 10.0}, confidence=0.9,
        strategy="text_llm", units_phrase="INR crore", period_ending="2026-03-31",
    )
    monkeypatch.setattr("nse_data.parsers.financial_extractor.extract",
                        lambda *a, **k: fake)
    rep = fr.run_extract_pass(conn, limit=20, use_llm=True, now=1700000000)
    assert rep["processed"] == 1 and rep["stored"] == 1   # only the result subject
    # second pass: already extracted -> dedup -> nothing processed
    rep2 = fr.run_extract_pass(conn, limit=20, use_llm=True, now=1700000000)
    assert rep2["processed"] == 0
