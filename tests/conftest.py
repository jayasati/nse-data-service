"""Pytest fixtures: in-memory SQLite + FakeSession test double."""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

import pytest


# ============================================================================
# Toy schema — one table per archetype
# ============================================================================

SCHEMA = """
CREATE TABLE snap (
    symbol   TEXT NOT NULL,
    as_of    INTEGER NOT NULL,
    price    REAL,
    PRIMARY KEY (symbol, as_of)
);

CREATE TABLE evt (
    fingerprint TEXT PRIMARY KEY,
    symbol      TEXT NOT NULL,
    subject     TEXT,
    received_at INTEGER
);

CREATE TABLE csv_data (
    date    TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    close   REAL,
    volume  INTEGER,
    PRIMARY KEY (date, symbol)
);

CREATE TABLE ref (
    symbol TEXT PRIMARY KEY,
    reason TEXT,
    stage  TEXT
);
"""


@pytest.fixture
def db():
    """Fresh in-memory SQLite with the toy schema. New connection per test."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()
    yield conn
    conn.close()


def count(conn, table: str) -> int:
    """Helper for tests — count rows in a table."""
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ============================================================================
# FakeSession — Session protocol test double
# ============================================================================

class FakeSession:
    """
    Implements the Session protocol against canned fixtures.

    Construct with a dict mapping fixture-key -> payload, where the key
    matches request.meta["fixture"] on the collector's planned Request.

    Optionally pass `errors={"fixture_key": SomeException(...)}` to make
    specific requests raise — used to verify FanoutCollector isolates
    per-call failures.
    """

    def __init__(
        self,
        json_fixtures: Mapping[str, Any] | None = None,
        text_fixtures: Mapping[str, str] | None = None,
        bytes_fixtures: Mapping[str, bytes] | None = None,
        errors: Mapping[str, BaseException] | None = None,
    ):
        self._json = dict(json_fixtures or {})
        self._text = dict(text_fixtures or {})
        self._bytes = dict(bytes_fixtures or {})
        self._errors = dict(errors or {})
        # Observable for assertions:
        self.calls: list[dict[str, Any]] = []

    def _route(self, kind: str, endpoint_name: str, target: str,
               referer: str | None, params: Mapping[str, Any] | None,
               store: dict) -> Any:
        # The fixture key comes from params["__fixture"] if present, else target.
        # Collectors signal which fixture they want via meta -> we surface it
        # through params here for simplicity. See DummyCollector.plan() below.
        key = (params or {}).get("__fixture", target) if params else target
        self.calls.append({
            "kind": kind, "endpoint": endpoint_name, "target": target,
            "referer": referer, "params": dict(params) if params else None,
            "fixture_key": key,
        })
        if key in self._errors:
            raise self._errors[key]
        if key not in store:
            raise KeyError(f"FakeSession has no {kind} fixture for {key!r}")
        return store[key]

    def get_json(self, endpoint_name, path, referer=None, params=None):
        return self._route("json", endpoint_name, path, referer, params, self._json)

    def get_text(self, endpoint_name, path, referer=None, params=None):
        return self._route("text", endpoint_name, path, referer, params, self._text)

    def get_bytes(self, endpoint_name, url, referer=None):
        return self._route("bytes", endpoint_name, url, referer, None, self._bytes)