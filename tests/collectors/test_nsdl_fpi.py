"""
NSDL FPI Daily Trends acceptance tests.

Parses the real captured Latest.aspx fixture (tests/fixtures/nsdl_fpi_latest.html,
report date 26-May-2026) — no live NSDL calls.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.nsdl_fpi import NsdlFpi, NsdlFpiParseError, _money

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
MIGRATION_DIR = Path(__file__).parent.parent.parent / "migrations"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    for sql in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(sql.read_text())
    yield conn
    conn.close()


def _html() -> str:
    return (FIXTURE_DIR / "nsdl_fpi_latest.html").read_text(encoding="utf-8", errors="replace")


def _rows():
    return NsdlFpi().normalize(_html(), Request(path_or_url="x"))


def test_money_parses_negatives_and_commas():
    assert _money("12,847.68") == 12847.68
    assert _money("(193.61)") == -193.61
    assert _money("0.00") == 0.0
    assert _money("-") is None
    assert _money("") is None


def test_date_and_asset_classes_parsed():
    rows = _rows()
    assert {r["as_of_date"] for r in rows} == {"2026-05-26"}
    assert {r["report_date_label"] for r in rows} == {"26-May-2026"}
    assets = {r["asset_class"] for r in rows}
    # Every asset class from the report, plus the grand Total.
    for a in ("Equity", "Debt-General Limit", "Debt-VRR", "Debt-FAR",
              "Hybrid", "Mutual Funds", "AIFs", "Total"):
        assert a in assets, f"missing asset class {a}"


def test_equity_stock_exchange_values():
    rows = _rows()
    eq_se = next(r for r in rows
                 if r["asset_class"] == "Equity"
                 and r["investment_route"] == "Stock Exchange")
    assert eq_se["gross_purchase_cr"] == 12847.68
    assert eq_se["gross_sales_cr"] == 10732.31
    assert eq_se["net_cr"] == 2115.37
    assert eq_se["net_usd_mn"] == 222.19
    # Conversion rate is carried across all rows from the first (rowspan) cell.
    assert eq_se["conversion_rate"] == pytest.approx(95.2047)


def test_negative_net_captured_as_negative():
    rows = _rows()
    # Debt-General Limit / Stock Exchange was a net sell: (193.61)
    debt_se = next(r for r in rows
                   if r["asset_class"] == "Debt-General Limit"
                   and r["investment_route"] == "Stock Exchange")
    assert debt_se["net_cr"] == -193.61
    assert debt_se["net_usd_mn"] == -20.34


def test_grand_total_row():
    rows = _rows()
    total = next(r for r in rows if r["asset_class"] == "Total")
    assert total["investment_route"] == "Total"
    assert total["net_cr"] == 2564.20
    assert total["net_usd_mn"] == 269.33


def test_subtotal_equals_component_sum():
    """The per-asset Sub-total net should equal its route components."""
    rows = _rows()
    eq = {r["investment_route"]: r for r in rows if r["asset_class"] == "Equity"}
    components = eq["Stock Exchange"]["net_cr"] + eq["Primary market & others"]["net_cr"]
    assert eq["Sub-total"]["net_cr"] == pytest.approx(components, abs=0.01)


def test_runs_end_to_end_and_persists(db):
    collector = NsdlFpi()
    # Stub the network fetch with the captured fixture.
    collector.fetch = lambda client: _html()  # type: ignore[assignment]
    report = collector.run(session=None, db=db)

    assert report.succeeded == 1
    assert report.failed == 0
    assert report.persist.inserted == report.rows_seen > 0

    n = db.execute("SELECT COUNT(*) FROM raw_nsdl_fpi_daily").fetchone()[0]
    assert n == report.persist.inserted

    # Re-running the same report is idempotent (upsert on the PK).
    report2 = collector.run(session=None, db=db)
    assert report2.persist.inserted == 0
    n2 = db.execute("SELECT COUNT(*) FROM raw_nsdl_fpi_daily").fetchone()[0]
    assert n2 == n


def test_parse_guard_raises_on_markup_drift():
    with pytest.raises(NsdlFpiParseError):
        NsdlFpi().normalize("<html><body>no report here</body></html>",
                            Request(path_or_url="x"))


def test_run_records_parse_failure_not_silent(db):
    collector = NsdlFpi()
    collector.fetch = lambda client: "<html>broken</html>"  # type: ignore[assignment]
    report = collector.run(session=None, db=db)
    assert report.failed == 1
    assert report.persist.inserted == 0
    assert report.errors and report.errors[0].exc_type == "NsdlFpiParseError"
