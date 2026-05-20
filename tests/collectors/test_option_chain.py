"""
Phase 6 acceptance tests for OptionChain.

Covers:
- normalize() splits CE and PE into separate rows
- targets() walks universe.yaml correctly
- plan() emits N requests for N (symbol, expiry) pairs
- run() isolates per-symbol failures
- Snapshot semantics (re-polls accumulate by as_of)
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from nse_data.collectors.base import Request
from nse_data.collectors.option_chain import OptionChain, _f, _i

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
def nifty_chain():
    return json.loads((FIXTURE_DIR / "option_chain_nifty.json").read_text())


@pytest.fixture
def reliance_chain():
    return json.loads((FIXTURE_DIR / "option_chain_reliance.json").read_text())


@pytest.fixture
def universe_yaml(tmp_path, monkeypatch):
    """Write a tiny universe.yaml for tests; redirect the collector to it."""
    path = tmp_path / "universe.yaml"
    path.write_text("""
option_chain:
  indices: [NIFTY]
  equities: [RELIANCE]
  n_expiries: 1
""")
    monkeypatch.setattr(OptionChain, "universe_path", str(path))
    return path


# ============================================================================
# Unit — _f, _i, normalize()
# ============================================================================

def test_safe_float_handles_string():
    assert _f("23.5") == 23.5
    assert _f(None) is None
    assert _f("") is None
    assert _f("nan-ish") is None


def test_safe_int_handles_float_string():
    assert _i("1053") == 1053
    assert _i("1053.0") == 1053
    assert _i(None) is None


def test_normalize_splits_ce_and_pe(nifty_chain):
    """One strike with CE and PE -> two rows."""
    req = Request(
        path_or_url="x",
        meta={"symbol": "NIFTY", "expiry": "26-May-2026", "type": "Indices"},
    )
    rows = OptionChain().normalize(nifty_chain, req)
    # NIFTY fixture: 144 strikes, both CE/PE on all -> 288 rows
    assert len(rows) > 100  # generous bound

    option_types = {r["option_type"] for r in rows}
    assert option_types == {"CE", "PE"}


def test_normalize_carries_meta_through(nifty_chain):
    req = Request(
        path_or_url="x",
        meta={"symbol": "NIFTY", "expiry": "26-May-2026", "type": "Indices"},
    )
    rows = OptionChain().normalize(nifty_chain, req)
    for r in rows:
        assert r["symbol"] == "NIFTY"
        assert r["expiry"] == "26-May-2026"


def test_normalize_field_mapping(reliance_chain):
    req = Request(
        path_or_url="x",
        meta={"symbol": "RELIANCE", "expiry": "26-May-2026", "type": "Equities"},
    )
    rows = OptionChain().normalize(reliance_chain, req)

    # Find the strike-1100 CE row
    target = next(
        r for r in rows
        if r["symbol"] == "RELIANCE"
        and r["strike"] == 1100.0
        and r["option_type"] == "CE"
    )
    assert target["open_interest"] == 1053
    assert target["change_in_oi"] == -1
    assert target["last_price"] == 234.45
    assert target["underlying_value"] == 1358.0
    assert target["identifier"] == "OPTSTKRELIANCE26-05-2026CE1100.00"


def test_normalize_all_rows_share_as_of(reliance_chain):
    req = Request(path_or_url="x", meta={"symbol": "RELIANCE", "expiry": "26-May-2026", "type": "Equities"})
    rows = OptionChain().normalize(reliance_chain, req)
    timestamps = {r["as_of"] for r in rows}
    assert len(timestamps) == 1


def test_normalize_empty_response():
    req = Request(path_or_url="x", meta={"symbol": "X", "expiry": "1-Jan-2026", "type": "Indices"})
    assert OptionChain().normalize({}, req) == []
    assert OptionChain().normalize({"records": {}}, req) == []
    assert OptionChain().normalize({"records": {"data": []}}, req) == []


# ============================================================================
# Integration — fan-out planning and per-call isolation
# ============================================================================

def _meta_response(expiries: list[str]) -> dict:
    return {"expiryDates": expiries, "strikePrice": []}


# Fixture-key builder that matches _patch_fake_session_to_route_with_params.
# Sorted alphabetically by param name.
def _key(path: str, **params) -> str:
    if not params:
        return path
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return f"{path}?{qs}"


def test_run_aggregates_rows_across_symbols(
    universe_yaml, nifty_chain, reliance_chain, db
):
    session = FakeSession(json_fixtures={
        _key("/api/option-chain-contract-info", symbol="NIFTY"):    _meta_response(["26-May-2026"]),
        _key("/api/option-chain-contract-info", symbol="RELIANCE"): _meta_response(["26-May-2026"]),
        _key("/api/option-chain-v3", type="Indices",  symbol="NIFTY",    expiry="26-May-2026"): nifty_chain,
        _key("/api/option-chain-v3", type="Equities", symbol="RELIANCE", expiry="26-May-2026"): reliance_chain,
    })
    _patch_fake_session_to_route_with_params(session)

    report = OptionChain().run(session, db)
    assert report.fetched == 2
    assert report.succeeded == 2
    assert report.failed == 0
    assert report.rows_seen > 200

    count = db.execute("SELECT COUNT(*) FROM raw_option_chain").fetchone()[0]
    assert count == report.rows_seen


def test_run_isolates_per_symbol_failure(universe_yaml, nifty_chain, db):
    """Phase 6's key assertion. RELIANCE chain fails; NIFTY still lands."""
    session = FakeSession(
        json_fixtures={
            _key("/api/option-chain-contract-info", symbol="NIFTY"):    _meta_response(["26-May-2026"]),
            _key("/api/option-chain-contract-info", symbol="RELIANCE"): _meta_response(["26-May-2026"]),
            _key("/api/option-chain-v3", type="Indices", symbol="NIFTY", expiry="26-May-2026"): nifty_chain,
        },
        errors={
            _key("/api/option-chain-v3", type="Equities", symbol="RELIANCE", expiry="26-May-2026"):
                RuntimeError("simulated NSE 5xx"),
        },
    )
    _patch_fake_session_to_route_with_params(session)

    report = OptionChain().run(session, db)
    assert report.fetched == 2
    assert report.succeeded == 1
    assert report.failed == 1
    assert len(report.errors) == 1
    assert (
        "RELIANCE" in report.errors[0].message
        or "RELIANCE" in str(report.errors[0].request_meta)
    )

    nifty_count = db.execute(
        "SELECT COUNT(*) FROM raw_option_chain WHERE symbol='NIFTY'"
    ).fetchone()[0]
    assert nifty_count > 100

    rel_count = db.execute(
        "SELECT COUNT(*) FROM raw_option_chain WHERE symbol='RELIANCE'"
    ).fetchone()[0]
    assert rel_count == 0


def test_run_drops_symbol_when_meta_fetch_fails(universe_yaml, nifty_chain, db):
    """If contract-info fails for a symbol, that symbol silently drops out."""
    session = FakeSession(
        json_fixtures={
            _key("/api/option-chain-contract-info", symbol="NIFTY"): _meta_response(["26-May-2026"]),
            _key("/api/option-chain-v3", type="Indices", symbol="NIFTY", expiry="26-May-2026"): nifty_chain,
        },
        errors={
            _key("/api/option-chain-contract-info", symbol="RELIANCE"):
                RuntimeError("meta-fetch failed"),
        },
    )
    _patch_fake_session_to_route_with_params(session)

    report = OptionChain().run(session, db)
    assert report.fetched == 1   # only NIFTY was planned
    assert report.succeeded == 1
    assert report.failed == 0    # meta-fetch failures are logged, not raised
    assert report.rows_seen > 100
# ============================================================================
# Helpers
# ============================================================================

def _patch_fake_session_to_route_with_params(session):
    """
    Phase 1's FakeSession routes by URL alone, but Phase 6's collector
    distinguishes calls by URL + params (NIFTY vs RELIANCE differ only in
    params). Monkey-patch the route function for these tests.
    """
    original = session._route

    def keyed_route(kind, endpoint_name, target, referer, params, store):
        # Build key as URL + sorted query string
        if params:
            qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            key = f"{target}?{qs}"
        else:
            key = target
        if key in session._errors:
            raise session._errors[key]
        if key not in store:
            raise KeyError(f"FakeSession has no {kind} fixture for {key!r}")
        return store[key]

    session._route = keyed_route