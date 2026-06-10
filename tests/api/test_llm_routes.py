"""/api/llm/spend — the LLM cost-tracking endpoint and its aggregation."""
from __future__ import annotations

import datetime as _dt
import json

import pytest
from fastapi.testclient import TestClient

from nse_data.api.routes import llm as llm_routes
from nse_data.api.server import create_app
from nse_data.parsers.extractors.llm_client import DEFAULT_DAILY_CAP_USD


def test_cap_raised_to_25():
    """The user's accuracy-over-cost call (2026-06): cap is $25/day."""
    assert DEFAULT_DAILY_CAP_USD == 25.0


@pytest.fixture
def spend_file(tmp_path):
    p = tmp_path / "llm_spend.json"
    p.write_text(json.dumps({
        "2026-06-10": 3.21,
        "2026-06-09": 1.05,
        "2026-05-30": 7.5,
        "2026-05-29": 2.5,
        "bad-row": "not-a-number",
    }))
    return p


def test_spend_report_aggregates(spend_file):
    r = llm_routes.spend_report(
        path=spend_file, cap_usd=25.0, today=_dt.date(2026, 6, 10),
    )
    assert r["cap_usd"] == 25.0
    assert r["today"] == {"date": "2026-06-10", "spend_usd": 3.21,
                          "remaining_usd": 21.79}
    assert [d["date"] for d in r["daily"]] == [
        "2026-06-10", "2026-06-09", "2026-05-30", "2026-05-29"]   # bad row dropped
    assert r["monthly"] == [{"month": "2026-06", "usd": 4.26},
                            {"month": "2026-05", "usd": 10.0}]
    assert r["total_usd"] == 14.26


def test_spend_report_missing_file_is_empty(tmp_path):
    r = llm_routes.spend_report(path=tmp_path / "absent.json", cap_usd=25.0,
                                today=_dt.date(2026, 6, 10))
    assert r["daily"] == [] and r["monthly"] == [] and r["total_usd"] == 0.0
    assert r["today"]["remaining_usd"] == 25.0


def test_endpoint_serves_json(spend_file, monkeypatch):
    monkeypatch.setattr(llm_routes, "SPEND_LOG_PATH", spend_file)
    client = TestClient(create_app())
    r = client.get("/api/llm/spend")
    assert r.status_code == 200
    body = r.json()
    assert body["cap_usd"] == 25.0
    assert body["total_usd"] == 14.26
    # the dashboard page shell is served too
    page = client.get("/llm")
    assert page.status_code == 200 and "LLM Spend" in page.text
