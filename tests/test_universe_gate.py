"""The central universe gate — membership, grade headings, and the fail-open
safety rule (a missing table must never silently halt all collection)."""
from __future__ import annotations

import sqlite3

from nse_data.universe import (
    GRADE_CORE, GRADE_ETF, GRADE_ILLIQUID, GRADE_TRADEABLE, GRADE_VOLATILE,
    UniverseGate,
)


def _db(tmp_path, rows):
    p = tmp_path / "u.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE tradeable_universe (symbol TEXT PRIMARY KEY, grade TEXT)")
    c.executemany("INSERT INTO tradeable_universe (symbol, grade) VALUES (?,?)", rows)
    c.commit()
    c.close()
    return str(p)


def test_tracked_membership(tmp_path):
    db = _db(tmp_path, [
        ("HDFCBANK", GRADE_CORE), ("TATACAP", GRADE_TRADEABLE),
        ("YESBANK", GRADE_VOLATILE), ("SMALLCO", GRADE_ILLIQUID),
        ("NIFTYBEES", GRADE_ETF),
    ])
    g = UniverseGate(db)
    assert g.is_tracked("HDFCBANK") and g.is_tracked("TATACAP") and g.is_tracked("YESBANK")
    assert not g.is_tracked("SMALLCO")      # illiquid → not tracked
    assert not g.is_tracked("NIFTYBEES")    # etf → not tracked
    assert not g.is_tracked("NOTLISTED")    # absent → not tracked (table present)


def test_grade_headings(tmp_path):
    db = _db(tmp_path, [("HDFCBANK", GRADE_CORE), ("TATACAP", GRADE_TRADEABLE),
                        ("YESBANK", GRADE_VOLATILE), ("SMALLCO", GRADE_ILLIQUID)])
    g = UniverseGate(db)
    assert g.heading("HDFCBANK") == "CORE"
    assert g.heading("TATACAP") == "TRADEABLE"
    assert g.heading("YESBANK") == "VOLATILE"
    assert g.heading("SMALLCO") is None      # illiquid has no signal heading
    assert g.heading("NOTLISTED") is None


def test_case_insensitive_and_filter(tmp_path):
    db = _db(tmp_path, [("HDFCBANK", GRADE_CORE), ("SMALLCO", GRADE_ILLIQUID)])
    g = UniverseGate(db)
    assert g.is_tracked("hdfcbank")
    assert g.filter_tracked(["hdfcbank", "SMALLCO", "ZZZ"]) == ["hdfcbank"]
    assert g.tracked_symbols() == {"HDFCBANK"}


def test_fail_open_when_table_missing(tmp_path):
    # DB with no tradeable_universe table → gate must FAIL OPEN.
    p = tmp_path / "empty.db"
    sqlite3.connect(p).close()
    g = UniverseGate(str(p))
    assert g.is_tracked("ANYTHING") is True
    assert g.loaded_empty is True
    assert g.filter_tracked(["A", "B"]) == ["A", "B"]


def test_fail_open_when_table_empty(tmp_path):
    db = _db(tmp_path, [])
    g = UniverseGate(db)
    assert g.is_tracked("ANYTHING") is True   # empty table also fails open
    assert g.loaded_empty is True


def test_refresh_reloads(tmp_path):
    db = _db(tmp_path, [("HDFCBANK", GRADE_CORE)])
    g = UniverseGate(db)
    assert g.is_tracked("NEWCO") is False
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO tradeable_universe (symbol, grade) VALUES ('NEWCO', ?)",
                 (GRADE_TRADEABLE,))
    conn.commit(); conn.close()
    assert g.is_tracked("NEWCO") is False     # cached
    g.refresh()
    assert g.is_tracked("NEWCO") is True       # reloaded
