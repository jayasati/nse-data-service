"""Smoke test — RunReport JSON shape is stable and complete."""

from __future__ import annotations

import json

from nse_data.collectors.base import Request, Row, SnapshotCollector

from ..conftest import FakeSession   # type: ignore[import-not-found]


class TrivialCollector(SnapshotCollector):
    name = "trivial"
    table = "snap"
    pk_cols = ("symbol", "as_of")

    def plan(self, context=None):
        return [Request(
            path_or_url="/api/trivial",
            params={"__fixture": "t"},
            response_type="json",
        )]

    def normalize(self, data, request) -> list[Row]:
        return [{"symbol": "X", "as_of": 1, "price": 100.0}]


def test_run_report_has_stable_shape(db):
    session = FakeSession(json_fixtures={"t": [{"_": "_"}]})
    report = TrivialCollector().run(session, db)

    d = report.to_dict()
    assert set(d.keys()) == {
        "collector", "started_at", "finished_at", "duration_ms",
        "fetched", "succeeded", "failed", "rows_seen", "persist", "errors",
    }
    assert d["collector"] == "trivial"
    assert d["fetched"] == 1
    assert d["succeeded"] == 1
    assert d["failed"] == 0
    assert d["rows_seen"] == 1
    assert d["errors"] == []
    # persist is a PersistResult — asdict expands it
    assert set(d["persist"].keys()) == {
        "inserted", "updated", "deduped", "removed", "unchanged",
    }
    assert d["persist"]["inserted"] == 1


def test_run_report_serializes_to_json(db):
    """The scheduler will write this to logs; it must be JSON-clean."""
    session = FakeSession(json_fixtures={"t": [{"_": "_"}]})
    report = TrivialCollector().run(session, db)

    s = report.to_json()
    # Must round-trip without errors
    parsed = json.loads(s)
    assert parsed["collector"] == "trivial"
    assert parsed["persist"]["inserted"] == 1
    # And must include duration in ms
    assert isinstance(parsed["duration_ms"], int)


def test_run_report_captures_persist_error(db):
    """Persist failure goes on the report, not up as exception."""

    class BadCollector(SnapshotCollector):
        name = "bad"
        table = "snap"
        # Intentionally wrong pk so upsert_many fails at SQL prep time? No —
        # we trigger it by emitting a row with a column the table doesn't have.
        pk_cols = ("symbol", "as_of")

        def plan(self, context=None):
            return [Request(path_or_url="/x", params={"__fixture": "x"})]

        def normalize(self, data, request):
            return [{"symbol": "X", "as_of": 1, "nonexistent_col": 9.9}]

    session = FakeSession(json_fixtures={"x": [{"_": "_"}]})
    report = BadCollector().run(session, db)

    # Fetch + normalize succeeded, persist failed
    assert report.succeeded == 1
    assert len(report.errors) == 1
    assert report.errors[0].request_url == "<persist>"