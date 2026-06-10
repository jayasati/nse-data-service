"""LLM-first narrative extraction + reconciliation policy (P7 accuracy pass).

The LLM is faked — these pin the *policy*: LLM wins categoricals, regex wins
numeric unit disputes, regex fills gaps, malformed LLM output is discarded
field-wise, and everything degrades to pure regex with no client.
"""
from __future__ import annotations

import pytest

from nse_data.parsers.extractors.llm_client import LLMCallResult
from nse_data.parsers.narrative import llm_narrative
from nse_data.parsers.narrative.llm_narrative import narrative_fields


class FakeClient:
    def __init__(self, parsed_json):
        self.parsed_json = parsed_json
        self.calls = []

    def chat_completion(self, messages, response_format=None, max_tokens=0, **kw):
        self.calls.append(messages)
        return LLMCallResult(success=True, parsed_json=self.parsed_json, cost_usd=0.01)


@pytest.fixture
def fake_llm(monkeypatch):
    def install(parsed_json):
        client = FakeClient(parsed_json)
        monkeypatch.setattr(llm_narrative, "_get_client", lambda: client)
        return client
    return install


def test_no_client_degrades_to_regex(monkeypatch):
    monkeypatch.setattr(llm_narrative, "_get_client", lambda: None)
    d, cost = narrative_fields("The company raised its FY27 revenue guidance to 3%.")
    assert d["guidance"] == "raised" and cost == 0.0


def test_llm_wins_categorical_over_regex(fake_llm):
    # Regex reads the boilerplate "maintained"; the LLM judges the cut from context.
    fake_llm({"guidance": "cut"})
    d, cost = narrative_fields("The company maintained its margin guidance of 20%.")
    assert d["guidance"] == "cut"
    assert cost == 0.01


def test_regex_wins_numeric_unit_dispute(fake_llm):
    # LLM slips on lakh-crore grouping (says 1157.84); regex read the printed
    # ₹1,15,784 crore. Material disagreement → regex value survives.
    fake_llm({"order_inflow": 1157.84})
    d, _ = narrative_fields("The group received order inflow of ₹ 1,15,784 crore.")
    assert d["order_inflow"] == 115784.0


def test_llm_accepted_when_close_to_regex(fake_llm):
    fake_llm({"order_inflow": 115784.0})
    d, _ = narrative_fields("The group received order inflow of ₹ 1,15,784 crore.")
    assert d["order_inflow"] == 115784.0


def test_llm_fills_fields_regex_missed(fake_llm):
    # Unusual phrasing the regex can't anchor on; LLM reads it.
    fake_llm({"guidance": "cut", "cc_revenue_growth_pct": 2.1})
    d, _ = narrative_fields(
        "We now see full-year revenue toward the lower end of our earlier range."
    )
    assert d["guidance"] == "cut"
    assert d["cc_revenue_growth_pct"] == 2.1


def test_regex_fills_fields_llm_missed(fake_llm):
    fake_llm({"guidance": None})
    d, _ = narrative_fields("Interim dividend of Rs. 5/- per share was declared.")
    assert d["dividend"] == 5.0


def test_malformed_llm_values_discarded_fieldwise(fake_llm):
    fake_llm({
        "guidance": "slashed",          # out of vocabulary
        "fda_status": "ok",             # out of vocabulary
        "volume_growth": "4.5%",        # string, not a number
        "attrition_pct": 12000.0,       # absurd percentage
        "dividend": True,               # bool is not a number
        "tcv_usd_mn": 2500,             # valid — must survive
    })
    d, _ = narrative_fields("Quarterly update text with no regex-readable signals, volumes steady.")
    assert d["guidance"] is None
    assert d["fda_status"] is None
    assert d["volume_growth"] is None
    assert d["attrition_pct"] is None
    assert d["dividend"] is None
    assert d["tcv_usd_mn"] == 2500.0


def test_llm_failure_falls_back_to_regex(monkeypatch):
    class FailingClient:
        def chat_completion(self, *a, **k):
            return LLMCallResult(success=False, error="rate limited", cost_usd=0.0)
    monkeypatch.setattr(llm_narrative, "_get_client", lambda: FailingClient())
    d, _ = narrative_fields("The company raised its revenue guidance to 3%.")
    assert d["guidance"] == "raised"


def test_vision_builds_image_messages(monkeypatch, fake_llm):
    client = fake_llm({"grm_usd_bbl": 9.2})
    import nse_data.parsers.pdf_render as pdf_render
    monkeypatch.setattr(pdf_render, "render_pages", lambda data, **kw: [b"fakepng"])
    d, cost = llm_narrative.extract_narrative_vision(b"%PDF-fake", symbol="RELIANCE")
    assert d is not None and d["grm_usd_bbl"] == 9.2
    content = client.calls[0][0]["content"]
    assert any(part.get("type") == "image_url" for part in content if isinstance(part, dict))
    assert cost == 0.01


def test_vision_no_pages_returns_none(monkeypatch):
    import nse_data.parsers.pdf_render as pdf_render
    monkeypatch.setattr(pdf_render, "render_pages", lambda data, **kw: [])
    d, cost = llm_narrative.extract_narrative_vision(b"%PDF-fake")
    assert d is None and cost == 0.0
