"""Tests for the P8 feature store: snapshot assembly (sector ranking) + the forward
-return labeler. Assembly is tested with stub engines (no DB); the labeler against an
in-memory candle DB so the realised-excess maths is pinned exactly.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

from nse_data.research import snapshot as snap

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _ep(datestr: str) -> int:
    return int(_dt.datetime.strptime(datestr, "%Y-%m-%d").replace(tzinfo=_IST).timestamp())


# ---------------- snapshot assembly ----------------

class _Fake:
    def __init__(self, scores):
        self.scores = scores

    def score_universe(self, conn, syms, ep, sector_of):
        return {s: {"score": v} for s, v in self.scores.items()}


def test_compute_snapshot_ranks_by_composite(monkeypatch):
    q = _Fake({"A": 90.0, "B": 40.0, "C": 70.0})
    monkeypatch.setattr(snap, "ENGINES", (("quality", q),))
    monkeypatch.setattr(snap, "_COLS", ["quality"])
    monkeypatch.setattr(snap.composite_engine, "score_universe",
                        lambda *a, **k: {"A": {"score": 90.0}, "B": {"score": 40.0}, "C": {"score": 70.0}})
    rows = snap.compute_snapshot(None, ["A", "B", "C"], 0, lambda s: "tech")
    assert rows["A"]["sector_rank"] == 1            # best composite in sector
    assert rows["C"]["sector_rank"] == 2
    assert rows["B"]["sector_rank"] == 3
    assert rows["A"]["sector_n"] == 3
    assert rows["A"]["quality"] == 90.0


def test_compute_snapshot_skips_fully_unknown(monkeypatch):
    q = _Fake({"A": 55.0})                           # only A scored
    monkeypatch.setattr(snap, "ENGINES", (("quality", q),))
    monkeypatch.setattr(snap, "_COLS", ["quality"])
    monkeypatch.setattr(snap.composite_engine, "score_universe",
                        lambda *a, **k: {"A": {"score": 55.0}})
    rows = snap.compute_snapshot(None, ["A", "B"], 0, lambda s: "tech")
    assert "A" in rows and "B" not in rows           # B has nothing known → skipped


# ---------------- forward labeler ----------------

def _labeler_db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE factor_snapshot (snapshot_date TEXT, symbol TEXT, "
              "fwd_excess_30 REAL, fwd_excess_60 REAL, fwd_excess_90 REAL, fwd_excess_120 REAL, "
              "PRIMARY KEY(snapshot_date, symbol))")
    c.execute("CREATE TABLE raw_intraday_candles(symbol TEXT, interval TEXT, ts INT, close REAL)")
    # 160 daily sessions from 2025-01-01; STOCK +0.5%/day, NIFTYBEES flat at 100.
    base = _dt.date(2025, 1, 1)
    for i in range(160):
        d = (base + _dt.timedelta(days=i)).isoformat()
        c.execute("INSERT INTO raw_intraday_candles VALUES ('STOCK','day',?,?)",
                  (_ep(d), 100.0 * (1.005 ** i)))
        c.execute("INSERT INTO raw_intraday_candles VALUES ('NIFTYBEES','day',?,100.0)", (_ep(d),))
    return c, base


def test_label_matured_fills_excess(monkeypatch):
    c, base = _labeler_db()
    snap_date = base.isoformat()                     # snapshot on day 0
    c.execute("INSERT INTO factor_snapshot (snapshot_date, symbol) VALUES (?, 'STOCK')", (snap_date,))
    c.commit()
    # generic sector → NIFTYBEES benchmark (flat) → excess ≈ stock return − cost
    monkeypatch.setattr("nse_data.fundamentals.sectors.sector_class_for",
                        lambda s: type("S", (), {"value": "generic"})())
    filled = snap.label_matured(c, cost=0.5)
    assert filled[30] == 1 and filled[120] == 1
    row = c.execute("SELECT fwd_excess_30, fwd_excess_120 FROM factor_snapshot").fetchone()
    # 30 sessions of +0.5%/day vs flat bench: (1.005**30 − 1)*100 − 0.5 ≈ 15.6 − 0.5
    assert abs(row[0] - ((1.005 ** 30 - 1) * 100 - 0.5)) < 0.5
    assert row[1] > row[0]                            # 120d return > 30d return


def test_label_matured_idempotent(monkeypatch):
    c, base = _labeler_db()
    c.execute("INSERT INTO factor_snapshot (snapshot_date, symbol) VALUES (?, 'STOCK')",
              (base.isoformat(),))
    c.commit()
    monkeypatch.setattr("nse_data.fundamentals.sectors.sector_class_for",
                        lambda s: type("S", (), {"value": "generic"})())
    snap.label_matured(c)
    second = snap.label_matured(c)                    # already filled → nothing to do
    assert not any(second.values())
