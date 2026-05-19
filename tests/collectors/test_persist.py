"""Unit tests for storage.models — the four persist primitives."""

from __future__ import annotations

from nse_data.storage import models

from ..conftest import count   # type: ignore[import-not-found]


# ============================================================================
# upsert_many — used by SnapshotCollector and CsvCollector
# ============================================================================

def test_upsert_inserts_new_rows(db):
    rows = [
        {"symbol": "RELIANCE", "as_of": 100, "price": 2900.0},
        {"symbol": "TCS",      "as_of": 100, "price": 3850.0},
    ]
    r = models.upsert_many(db, "snap", rows, pk_cols=("symbol", "as_of"))
    assert r.inserted == 2
    assert r.updated == 0
    assert r.unchanged == 0
    assert count(db, "snap") == 2


def test_upsert_same_pk_same_values_is_unchanged(db):
    rows = [{"symbol": "RELIANCE", "as_of": 100, "price": 2900.0}]
    models.upsert_many(db, "snap", rows, pk_cols=("symbol", "as_of"))
    r = models.upsert_many(db, "snap", rows, pk_cols=("symbol", "as_of"))
    assert r.inserted == 0
    assert r.updated == 0
    assert r.unchanged == 1
    assert count(db, "snap") == 1


def test_upsert_same_pk_different_value_is_updated(db):
    models.upsert_many(
        db, "snap",
        [{"symbol": "RELIANCE", "as_of": 100, "price": 2900.0}],
        pk_cols=("symbol", "as_of"),
    )
    r = models.upsert_many(
        db, "snap",
        [{"symbol": "RELIANCE", "as_of": 100, "price": 2950.0}],
        pk_cols=("symbol", "as_of"),
    )
    assert r.updated == 1
    assert r.unchanged == 0
    new_price = db.execute(
        "SELECT price FROM snap WHERE symbol=? AND as_of=?", ("RELIANCE", 100)
    ).fetchone()[0]
    assert new_price == 2950.0


def test_upsert_different_as_of_is_a_new_row(db):
    """The snapshot pattern: same symbol at later timestamp = new row, not update."""
    models.upsert_many(
        db, "snap",
        [{"symbol": "RELIANCE", "as_of": 100, "price": 2900.0}],
        pk_cols=("symbol", "as_of"),
    )
    r = models.upsert_many(
        db, "snap",
        [{"symbol": "RELIANCE", "as_of": 200, "price": 2950.0}],
        pk_cols=("symbol", "as_of"),
    )
    assert r.inserted == 1
    assert count(db, "snap") == 2


# ============================================================================
# insert_ignore — used by EventCollector
# ============================================================================

def test_insert_ignore_dedups_on_fingerprint(db):
    row = {"fingerprint": "abc123", "symbol": "RELIANCE",
           "subject": "Board Meeting", "received_at": 100}
    r1 = models.insert_ignore(db, "evt", [row], key_cols=["fingerprint"])
    r2 = models.insert_ignore(db, "evt", [row], key_cols=["fingerprint"])
    assert r1.inserted == 1
    assert r2.inserted == 0
    assert r2.deduped == 1
    assert count(db, "evt") == 1


def test_insert_ignore_admits_new_fingerprints(db):
    models.insert_ignore(
        db, "evt",
        [{"fingerprint": "abc123", "symbol": "RELIANCE",
          "subject": "Board Meeting", "received_at": 100}],
        key_cols=["fingerprint"],
    )
    r = models.insert_ignore(
        db, "evt",
        [
            {"fingerprint": "abc123", "symbol": "RELIANCE",
             "subject": "Board Meeting", "received_at": 100},
            {"fingerprint": "def456", "symbol": "TCS",
             "subject": "Dividend", "received_at": 200},
        ],
        key_cols=["fingerprint"],
    )
    assert r.inserted == 1
    assert r.deduped == 1


# ============================================================================
# replace_all — used by ReferenceCollector with replace_strategy='replace_all'
# ============================================================================

def test_replace_all_wipes_and_reloads(db):
    models.replace_all(db, "ref", [
        {"symbol": "A", "reason": "x", "stage": "1"},
        {"symbol": "B", "reason": "y", "stage": "1"},
    ])
    assert count(db, "ref") == 2

    r = models.replace_all(db, "ref", [
        {"symbol": "C", "reason": "z", "stage": "2"},
    ])
    assert r.inserted == 1
    assert r.removed == 2
    assert count(db, "ref") == 1
    remaining = db.execute("SELECT symbol FROM ref").fetchone()[0]
    assert remaining == "C"


# ============================================================================
# diff_upsert — used by ReferenceCollector (default)
# ============================================================================

def test_diff_upsert_classifies_add_remove_update_unchanged(db):
    # Seed initial state
    models.diff_upsert(db, "ref", [
        {"symbol": "A", "reason": "ASM",  "stage": "1"},
        {"symbol": "B", "reason": "GSM",  "stage": "1"},
        {"symbol": "C", "reason": "T2T",  "stage": "1"},
    ], key_cols=("symbol",))

    # New snapshot: A unchanged, B updated, C removed, D added
    r = models.diff_upsert(db, "ref", [
        {"symbol": "A", "reason": "ASM",  "stage": "1"},  # unchanged
        {"symbol": "B", "reason": "GSM",  "stage": "2"},  # updated
        {"symbol": "D", "reason": "ASM",  "stage": "1"},  # inserted
    ], key_cols=("symbol",))

    assert r.inserted == 1
    assert r.updated == 1
    assert r.unchanged == 1
    assert r.removed == 1
    assert count(db, "ref") == 3
    assert db.execute(
        "SELECT stage FROM ref WHERE symbol='B'"
    ).fetchone()[0] == "2"