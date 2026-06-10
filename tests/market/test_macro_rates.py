"""Macro rate state + CSV import (market/macro_rates.py, Week 17.5 S6)."""
from __future__ import annotations

import sqlite3

import pytest

from nse_data.market import macro_rates as mr
from nse_data.storage.db import apply_migrations


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    apply_migrations(c, "migrations")
    yield c
    c.close()


def test_record_and_macro_state_sbi_backdrop(conn):
    # Q4 FY26 backdrop: Dec rate cut + 10Y hardening toward ~7%
    mr.record_rates(conn, "2025-09-30", repo_rate=6.50, gsec_10y_yield=6.55)
    mr.record_rates(conn, "2025-12-31", repo_rate=6.25, gsec_10y_yield=6.62)
    mr.record_rates(conn, "2026-01-31", repo_rate=6.00, gsec_10y_yield=6.80)
    mr.record_rates(conn, "2026-03-31", repo_rate=6.00, gsec_10y_yield=6.98)
    st = mr.macro_state(conn)
    assert st["rising_yields"] is True
    assert st["repo_cut_recent"] is True
    assert st["gsec_10y_qoq_bps"] >= 15
    cls, note = mr.bfsi_earnings_risk(st)
    assert cls == mr.NIM_TREASURY_RISK


def test_record_rates_partial_update_keeps_other(conn):
    mr.record_rates(conn, "2026-03-31", repo_rate=6.00, gsec_10y_yield=6.98)
    mr.record_rates(conn, "2026-03-31", gsec_10y_yield=7.05)   # update yield only
    row = conn.execute(
        "SELECT repo_rate, gsec_10y_yield FROM raw_macro_rates WHERE as_of_date='2026-03-31'"
    ).fetchone()
    assert row == (6.00, 7.05)


def test_benign_backdrop_no_flags(conn):
    mr.record_rates(conn, "2025-09-30", repo_rate=6.00, gsec_10y_yield=6.50)
    mr.record_rates(conn, "2026-03-31", repo_rate=6.00, gsec_10y_yield=6.52)  # flat
    st = mr.macro_state(conn)
    assert st["rising_yields"] is False and st["repo_cut_recent"] is False
    assert mr.bfsi_earnings_risk(st) == (None, "")


def test_import_csv_fbil_style(conn, tmp_path):
    # FBIL/DBIE-ish export: a date column + a 10Y yield column, varied header text
    p = tmp_path / "gsec.csv"
    p.write_text(
        "Date,10 Year G-Sec Yield (%)\n"
        "31-12-2025,6.62\n"
        "31-01-2026,6.80\n"
        "31-03-2026,6.98\n"
    )
    rep = mr.import_rates_csv(conn, p)
    assert rep["imported"] == 3
    assert rep["gsec_col"] == "10 Year G-Sec Yield (%)"
    row = conn.execute(
        "SELECT gsec_10y_yield FROM raw_macro_rates WHERE as_of_date='2026-03-31'"
    ).fetchone()
    assert row[0] == 6.98


def test_import_csv_explicit_columns_and_repo(conn, tmp_path):
    p = tmp_path / "rates.csv"
    p.write_text("dt,repo,yld10\n2026-03-31,6.00,6.98\n")
    rep = mr.import_rates_csv(conn, p, date_col="dt", repo_col="repo", gsec_col="yld10")
    assert rep["imported"] == 1
    row = conn.execute(
        "SELECT repo_rate, gsec_10y_yield FROM raw_macro_rates WHERE as_of_date='2026-03-31'"
    ).fetchone()
    assert row == (6.00, 6.98)


def test_import_csv_undetectable_raises(conn, tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError):
        mr.import_rates_csv(conn, p)
