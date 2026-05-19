"""
Integration tests — one DummyCollector per archetype, run against FakeSession.

These prove the base.Collector.run() orchestration drives each archetype's
persist() correctly, with errors captured (not raised) per-request.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Mapping, Sequence

import pytest

from nse_data.collectors.base import (
    CsvCollector,
    EventCollector,
    FanoutCollector,
    PersistResult,
    ReferenceCollector,
    Request,
    Row,
    SnapshotCollector,
)

from ..conftest import FakeSession, count   # type: ignore[import-not-found]


# ============================================================================
# A — SnapshotCollector
# ============================================================================

class DummySnapshot(SnapshotCollector):
    name = "dummy_snap"
    table = "snap"
    pk_cols = ("symbol", "as_of")

    def plan(self, context=None):
        return [Request(
            path_or_url="/api/oi-spurts",
            params={"__fixture": "snap_v1"},
            response_type="json",
        )]

    def normalize(self, data, request):
        as_of = (request.meta or {}).get("as_of", 1000)
        return [
            {"symbol": d["sym"], "as_of": as_of, "price": d["px"]}
            for d in data
        ]


def test_snapshot_inserts_rows(db):
    session = FakeSession(json_fixtures={
        "snap_v1": [{"sym": "RELIANCE", "px": 2900.0}, {"sym": "TCS", "px": 3850.0}]
    })
    report = DummySnapshot().run(session, db)
    assert report.fetched == 1 and report.succeeded == 1
    assert report.rows_seen == 2
    assert report.persist == PersistResult(inserted=2)
    assert count(db, "snap") == 2


# ============================================================================
# B — EventCollector
# ============================================================================

class DummyAnnouncements(EventCollector):
    name = "dummy_ann"
    table = "evt"

    def plan(self, context=None):
        return [Request(
            path_or_url="/api/corporate-announcements",
            params={"__fixture": "ann_v1"},
            response_type="json",
        )]

    def normalize(self, data, request):
        return [
            {"symbol": d["symbol"], "subject": d["subject"],
             "received_at": d["ts"]}
            for d in data
        ]

    def fingerprint(self, row: Row) -> str:
        key = f"{row['symbol']}|{row['subject']}|{row['received_at']}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


def test_event_dedups_on_rerun(db):
    payload = [
        {"symbol": "RELIANCE", "subject": "Board Meeting", "ts": 1000},
        {"symbol": "TCS",      "subject": "Dividend",      "ts": 1100},
    ]
    session = FakeSession(json_fixtures={"ann_v1": payload})

    r1 = DummyAnnouncements().run(session, db)
    assert r1.persist.inserted == 2
    assert count(db, "evt") == 2

    r2 = DummyAnnouncements().run(session, db)
    assert r2.persist.inserted == 0
    assert r2.persist.deduped == 2
    assert count(db, "evt") == 2


def test_event_inserts_only_new_on_partial_overlap(db):
    session = FakeSession(json_fixtures={"ann_v1": [
        {"symbol": "RELIANCE", "subject": "Board Meeting", "ts": 1000},
    ]})
    DummyAnnouncements().run(session, db)

    session2 = FakeSession(json_fixtures={"ann_v1": [
        {"symbol": "RELIANCE", "subject": "Board Meeting", "ts": 1000},
        {"symbol": "TCS",      "subject": "Dividend",      "ts": 1100},
    ]})
    r = DummyAnnouncements().run(session2, db)
    assert r.persist.inserted == 1
    assert r.persist.deduped == 1


# ============================================================================
# C — CsvCollector
# ============================================================================

class DummyBhavcopy(CsvCollector):
    name = "dummy_bhav"
    table = "csv_data"
    pk_cols = ("date", "symbol")
    response_type = "bytes"

    def url_for_date(self, d: date) -> str:
        return f"https://example.com/bhav_{d.strftime('%d%m%Y')}.csv"

    def plan(self, context=None):
        # Override to inject the fixture key
        target = (context or {}).get("date") or date.today()
        if isinstance(target, str):
            target = date.fromisoformat(target)
        return [Request(
            path_or_url=self.url_for_date(target),
            response_type="bytes",
            meta={"date": target.isoformat()},
            # Route by date so the same collector can serve multiple dates
            params=None,
        )]

    def normalize(self, data: bytes, request):
        # Parse our toy "CSV": symbol,close,volume per line
        d = (request.meta or {}).get("date")
        rows = []
        for line in data.decode().strip().splitlines():
            sym, close, vol = line.split(",")
            rows.append({"date": d, "symbol": sym,
                         "close": float(close), "volume": int(vol)})
        return rows


def test_csv_idempotent_for_same_date(db):
    # FakeSession routes by target URL for bytes (no params)
    url = DummyBhavcopy().url_for_date(date(2026, 5, 15))
    csv = b"RELIANCE,2900.0,1000000\nTCS,3850.0,500000\n"
    session = FakeSession(bytes_fixtures={url: csv})

    r1 = DummyBhavcopy().run_for_date(session, db, date(2026, 5, 15))
    assert r1.persist.inserted == 2

    r2 = DummyBhavcopy().run_for_date(session, db, date(2026, 5, 15))
    assert r2.persist.inserted == 0
    assert r2.persist.unchanged == 2
    assert count(db, "csv_data") == 2


# ============================================================================
# D — ReferenceCollector
# ============================================================================

class DummyBlacklist(ReferenceCollector):
    name = "dummy_blk"
    table = "ref"
    key_cols = ("symbol",)
    replace_strategy = "diff"

    def plan(self, context=None):
        return [Request(
            path_or_url="/api/surveillance",
            params={"__fixture": "blk_v1"},
            response_type="json",
        )]

    def normalize(self, data, request):
        return [
            {"symbol": d["sym"], "reason": d["reason"], "stage": d["stage"]}
            for d in data
        ]


def test_reference_diff_tracks_changes(db):
    s1 = FakeSession(json_fixtures={"blk_v1": [
        {"sym": "A", "reason": "ASM", "stage": "1"},
        {"sym": "B", "reason": "GSM", "stage": "1"},
    ]})
    r1 = DummyBlacklist().run(s1, db)
    assert r1.persist.inserted == 2

    s2 = FakeSession(json_fixtures={"blk_v1": [
        {"sym": "A", "reason": "ASM", "stage": "1"},   # unchanged
        {"sym": "B", "reason": "GSM", "stage": "2"},   # stage bumped
        {"sym": "C", "reason": "T2T", "stage": "1"},   # new entry
        # A removed? no, A unchanged. (Just illustrating)
    ]})
    r2 = DummyBlacklist().run(s2, db)
    assert r2.persist.inserted == 1
    assert r2.persist.updated == 1
    assert r2.persist.unchanged == 1
    assert r2.persist.removed == 0


# ============================================================================
# E — FanoutCollector
# ============================================================================

class DummyOptionChain(FanoutCollector):
    name = "dummy_oc"
    table = "snap"
    pk_cols = ("symbol", "as_of")

    def targets(self, context=None):
        return (context or {}).get("symbols", ["NIFTY", "BANKNIFTY", "RELIANCE"])

    def plan_one(self, target):
        return Request(
            path_or_url="/api/option-chain-equities",
            params={"__fixture": f"oc_{target}", "symbol": target},
            response_type="json",
            meta={"symbol": target},
        )

    def normalize(self, data, request):
        sym = request.meta["symbol"]
        return [{"symbol": sym, "as_of": data["ts"], "price": data["spot"]}]


def test_fanout_aggregates_rows_per_target(db):
    session = FakeSession(json_fixtures={
        "oc_NIFTY":     {"ts": 1000, "spot": 22000.0},
        "oc_BANKNIFTY": {"ts": 1000, "spot": 48000.0},
        "oc_RELIANCE":  {"ts": 1000, "spot":  2900.0},
    })
    report = DummyOptionChain().run(session, db)
    assert report.fetched == 3
    assert report.succeeded == 3
    assert report.failed == 0
    assert report.rows_seen == 3
    assert report.persist.inserted == 3


def test_fanout_isolates_per_call_errors(db):
    """One symbol failing must not kill the others. Phase 6 relies on this."""
    session = FakeSession(
        json_fixtures={
            "oc_NIFTY":     {"ts": 1000, "spot": 22000.0},
            "oc_RELIANCE":  {"ts": 1000, "spot":  2900.0},
        },
        errors={"oc_BANKNIFTY": RuntimeError("simulated NSE 5xx")},
    )
    report = DummyOptionChain().run(session, db)

    assert report.fetched == 3
    assert report.succeeded == 2
    assert report.failed == 1
    assert len(report.errors) == 1
    assert report.errors[0].exc_type == "RuntimeError"
    assert "simulated NSE 5xx" in report.errors[0].message
    # The two successful symbols still landed
    assert report.persist.inserted == 2
    assert count(db, "snap") == 2