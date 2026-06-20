"""Tests for R13 F&O-ban collector parsing + the gate lookup."""
from __future__ import annotations

import sqlite3

from nse_data.collectors.base import Request
from nse_data.collectors.fno_ban import FnoBan, is_fno_banned, latest_ban_date

_SAMPLE = """As on 20-Jun-2026
Sr.No.,Symbol
1,ABCAPITAL
2,BANDHANBNK
3,HINDCOPPER
"""


def test_parser_extracts_symbols_only():
    req = Request(path_or_url="x", response_type="text", meta={"date": "2026-06-20"})
    rows = FnoBan().normalize(_SAMPLE, req)
    syms = {r["symbol"] for r in rows}
    assert syms == {"ABCAPITAL", "BANDHANBNK", "HINDCOPPER"}      # headers/serials excluded
    assert all(r["ban_date"] == "2026-06-20" for r in rows)


def test_parser_handles_bytes_and_empty():
    req = Request(path_or_url="x", response_type="text", meta={"date": "2026-06-20"})
    assert FnoBan().normalize(b"1,RELIANCE\n", req)[0]["symbol"] == "RELIANCE"   # bytes ok
    assert FnoBan().normalize("As on 20-Jun-2026\nNil\n", req) == []             # no securities


def _db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE raw_fno_ban (symbol TEXT, ban_date TEXT, fetched_at INTEGER, "
                 "PRIMARY KEY (symbol, ban_date))")
    conn.executemany("INSERT INTO raw_fno_ban (symbol, ban_date) VALUES (?,?)", rows)
    conn.commit()
    return conn


def test_gate_uses_latest_ban_date():
    conn = _db([("ABCAPITAL", "2026-06-20"), ("OLDNAME", "2026-05-01")])
    assert latest_ban_date(conn) == "2026-06-20"
    assert is_fno_banned(conn, "ABCAPITAL") is True
    assert is_fno_banned(conn, "OLDNAME") is False        # only in the stale list → not banned now
    assert is_fno_banned(conn, "TCS") is False


def test_gate_fails_open_without_table():
    conn = sqlite3.connect(":memory:")
    assert is_fno_banned(conn, "TCS") is False            # no table → fail-open
    assert latest_ban_date(conn) is None
