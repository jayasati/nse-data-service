"""LLM spend read API — daily/monthly cost tracking for the dashboard.

Reads the cumulative spend log every LLMClient call appends to
(``data/llm_spend.json``: {"YYYY-MM-DD": usd}). One endpoint, no DB:

    GET /api/llm/spend  →  {cap_usd, today, daily[], monthly[]}

``today`` carries spend vs cap so the dashboard can show how much accuracy
headroom is left mid-results-day (a cap-hit degrades narrative reads to
regex-only).
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ...parsers.extractors.llm_client import DEFAULT_DAILY_CAP_USD, SPEND_LOG_PATH

router = APIRouter(prefix="/api/llm")

_DAILY_LIMIT = 90    # most-recent days returned


def spend_report(
    path: Path,
    cap_usd: float = DEFAULT_DAILY_CAP_USD,
    today: _dt.date | None = None,
) -> dict:
    """Pure aggregation of the spend log → the API payload."""
    today = today or _dt.date.today()
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        raw = {}
    days = sorted(
        ((d, float(v)) for d, v in raw.items() if isinstance(v, (int, float))),
        reverse=True,
    )
    monthly: dict[str, float] = {}
    for d, v in days:
        monthly[d[:7]] = monthly.get(d[:7], 0.0) + v
    today_spend = raw.get(today.isoformat(), 0.0)
    return {
        "cap_usd": cap_usd,
        "today": {
            "date": today.isoformat(),
            "spend_usd": round(float(today_spend), 4),
            "remaining_usd": round(max(0.0, cap_usd - float(today_spend)), 4),
        },
        "daily": [{"date": d, "usd": round(v, 4)} for d, v in days[:_DAILY_LIMIT]],
        "monthly": [{"month": m, "usd": round(v, 4)}
                    for m, v in sorted(monthly.items(), reverse=True)],
        "total_usd": round(sum(v for _, v in days), 4),
    }


@router.get("/spend")
def spend() -> JSONResponse:
    # module attribute resolved per request (testable via monkeypatch)
    import nse_data.api.routes.llm as _self

    return JSONResponse(spend_report(path=_self.SPEND_LOG_PATH))
