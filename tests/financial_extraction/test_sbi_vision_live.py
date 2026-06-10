"""Live vision-accuracy gate on the real SBI Q4 FY26 filing (Week 17.5).

Runs the actual gpt-4o vision extractor on the archived SBI result PDF and
asserts the BFSI lines come out correct after the dense-bank-table hardening
(300 DPI / P&L page only, identity corrections, TOTAL-INCOME text anchor,
GNPA/NNPA text override). SKIPPED automatically when Azure OpenAI credentials
are absent, so it's a no-op in CI without creds and a real check where they exist.

Ground truth: tests/financial_extraction/ground_truth_bfsi/SBIN_Q4FY26.yaml
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "pdfs" / "sbin_q4fy26_5808055808055808.pdf"
)
_HAS_CREDS = all(
    os.environ.get(k)
    for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT_NAME")
)

pytestmark = [
    pytest.mark.skipif(not _HAS_CREDS, reason="Azure OpenAI credentials not set"),
    pytest.mark.skipif(not _FIXTURE.exists(), reason="SBI fixture PDF not present"),
]

# (field, expected, abs/rel tolerance) — actuals from the filing.
_EXPECT = [
    ("pat_cr", 19683.75, 1.0),
    ("operating_profit_cr", 27704.18, 1.0),
    ("total_income_cr", 140411.77, 50.0),
    ("net_interest_income_cr", 44380.0, 400.0),   # ~1% — small other-income digit noise
    ("revenue_cr", 123097.67, 400.0),
    ("gross_npa_pct", 1.49, 0.01),
    ("net_npa_pct", 0.39, 0.01),
]


def test_sbi_q4fy26_vision_extraction_accurate():
    from nse_data.parsers.financial_extractor import extract

    res = extract(str(_FIXTURE), use_llm_fallback=True, symbol="SBIN",
                  subject="Outcome of Board Meeting", broadcast_dt="08-May-2026 14:01:38")
    assert res.strategy.startswith("vision"), res.strategy
    assert res.period_ending == "2026-03-31"
    f = res.fields
    bad = []
    for key, exp, tol in _EXPECT:
        v = f.get(key)
        if v is None or abs(v - exp) > tol:
            bad.append(f"{key}={v} (want {exp}±{tol})")
    assert not bad, "BFSI extraction off: " + "; ".join(bad)
