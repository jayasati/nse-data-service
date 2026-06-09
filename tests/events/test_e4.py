"""E4 tests: consensus ingestion, true surprise, scraper scaffold, fold-in."""
from __future__ import annotations

import datetime as dt

from nse_data.events import consensus, estimate_scraper, matcher, pre_screen
from nse_data.events import expectation as exp
from nse_data.fundamentals import from_results as fr
from nse_data.storage import db as dbmod

import pytest


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.open_db(str(tmp_path / "t.db"))
    dbmod.apply_migrations(c, migrations_dir="migrations")
    yield c
    c.close()


# --------------------------------------------------------------------------- #
# consensus.py
# --------------------------------------------------------------------------- #

def test_ingest_and_nearest_exact_and_tolerant(conn):
    consensus.ingest_estimates(conn, [
        {"symbol": "TCS", "period_ending": "2026-03-31", "pat_est_cr": 12000, "rev_est_cr": 64000},
    ], source="manual", as_of=100)
    exact = consensus.nearest_estimate(conn, "TCS", "2026-03-31")
    assert exact["pat_est_cr"] == 12000
    # a query a few days off still matches within tolerance
    near = consensus.nearest_estimate(conn, "TCS", "2026-03-28")
    assert near is not None and near["pat_est_cr"] == 12000
    # far away -> no match
    assert consensus.nearest_estimate(conn, "TCS", "2025-12-31") is None


def test_latest_source_wins(conn):
    consensus.upsert_estimate(conn, symbol="X", period_ending="2026-03-31",
                              pat_est_cr=100, source="a", as_of=1)
    consensus.upsert_estimate(conn, symbol="X", period_ending="2026-03-31",
                              pat_est_cr=200, source="b", as_of=2)
    assert consensus.nearest_estimate(conn, "X", "2026-03-31")["pat_est_cr"] == 200


def test_estimate_surprise_and_classify():
    assert consensus.estimate_surprise(110, 100) == 10.0
    assert consensus.estimate_surprise(90, 100) == -10.0
    assert consensus.estimate_surprise(100, 0) is None
    # PAT beats estimate by 50% -> beat
    assert consensus.classify_estimate_surprise({"pat_cr": 15}, {"pat_est_cr": 10}) == (1, 50.0)
    # PAT misses by 20% -> miss
    assert consensus.classify_estimate_surprise({"pat_cr": 8}, {"pat_est_cr": 10}) == (-1, 20.0)
    # within +/-3% -> in-line
    assert consensus.classify_estimate_surprise({"pat_cr": 101}, {"pat_est_cr": 100})[0] == 0
    assert consensus.classify_estimate_surprise({"pat_cr": 10}, None) == (0, 0.0)


# --------------------------------------------------------------------------- #
# matcher fold-in: consensus preferred over trend
# --------------------------------------------------------------------------- #

def _store_actual(conn, symbol, period, rev, pat):
    fr.persist_extraction(
        conn, symbol=symbol, period_ending=period, scope="standalone",
        fields={"revenue_cr": rev, "pat_cr": pat, "total_income_cr": rev},
        units_phrase="INR crore", confidence=1.0, strategy="vision",
        source_fingerprint=None, broadcast_dt=None, now=1)


def test_evidence_uses_consensus_when_present(conn):
    _store_actual(conn, "ACME", "2026-03-31", rev=140, pat=15)
    # estimate expected only 10cr PAT -> actual 15 is a clear beat
    consensus.upsert_estimate(conn, symbol="ACME", period_ending="2026-03-31",
                              pat_est_cr=10, source="manual", as_of=1)
    ev = matcher.build_earnings_evidence(conn, "ACME", reaction_direction="long")
    assert ev["surprise_basis"] == "consensus"
    assert ev["surprise_sign"] == 1 and ev["confirms_direction"] is True
    assert ev["consensus_pat_est_cr"] == 10


def test_evidence_falls_back_to_trend_without_estimate(conn):
    _store_actual(conn, "ACME", "2025-03-31", rev=100, pat=10)
    _store_actual(conn, "ACME", "2026-03-31", rev=140, pat=15)
    ev = matcher.build_earnings_evidence(conn, "ACME", reaction_direction="long")
    assert ev["surprise_basis"] == "trend"
    assert ev["surprise_sign"] == 1   # +50% YoY PAT


# --------------------------------------------------------------------------- #
# scraper scaffold
# --------------------------------------------------------------------------- #

def test_normalise_record_aliases_and_coercion():
    rec = {"ticker": "TCS", "quarter_end": "2026-03-31",
           "revenue_estimate": "64,000", "eps": "120.5"}
    out = estimate_scraper.normalise_record(rec)
    assert out["symbol"] == "TCS" and out["period_ending"] == "2026-03-31"
    assert out["rev_est_cr"] == 64000.0 and out["eps_est"] == 120.5
    assert estimate_scraper.normalise_record({"foo": 1}) is None   # no symbol/period


def test_fetch_and_ingest_with_fake_fetcher(conn):
    def fake_fetcher(symbol):
        return {"symbol": symbol, "period_ending": "2026-03-31", "pat_est_cr": 500}
    n = estimate_scraper.fetch_and_ingest(conn, ["A", "B"], fetcher=fake_fetcher, source="test")
    assert n == 2
    assert consensus.nearest_estimate(conn, "A", "2026-03-31")["pat_est_cr"] == 500


def test_fetch_and_ingest_tolerates_fetcher_errors(conn):
    def flaky(symbol):
        if symbol == "BAD":
            raise RuntimeError("boom")
        return {"symbol": symbol, "period_ending": "2026-03-31", "eps_est": 5}
    n = estimate_scraper.fetch_and_ingest(conn, ["BAD", "OK"], fetcher=flaky, source="t")
    assert n == 1   # BAD skipped, OK ingested


# --------------------------------------------------------------------------- #
# pre_screen consensus population
# --------------------------------------------------------------------------- #

def test_pre_screen_populates_consensus(conn):
    # estimate for the quarter ending on/before the event date (2026-04-25 -> 2026-03-31)
    consensus.upsert_estimate(conn, symbol="ACME", period_ending="2026-03-31",
                              rev_est_cr=1000, eps_est=12.0, source="manual", as_of=1)
    setup = pre_screen.build_setup(conn, "ACME", "2026-04-25", dt.date(2026, 4, 20))
    assert setup["consensus_rev_est"] == 1000
    assert setup["consensus_eps_est"] == 12.0
    msg = exp.build_flag_message("ACME", "2026-04-25", setup)
    assert "Consensus" in msg
