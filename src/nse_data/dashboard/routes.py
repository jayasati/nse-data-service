"""HTML page routes — serve the static dashboard shells.

One route per page; each returns a shell from static/pages/. The shells pull their data from
the api/ surface over fetch(), so there's no logic here.

All shells are served `Cache-Control: no-cache` (same policy as the /static bundle) so the
browser always revalidates the page HTML — otherwise a cached shell can keep an old header/nav
after a deploy. Cheap 304s when unchanged; instant pickup when it changes.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

# The static bundle lives alongside this package; the app mounts it at /static.
STATIC_DIR = Path(__file__).parent / "static"
PAGES_DIR = STATIC_DIR / "pages"
_NO_CACHE = {"Cache-Control": "no-cache"}

router = APIRouter()


def _page(name: str) -> FileResponse:
    return FileResponse(PAGES_DIR / name, headers=_NO_CACHE)


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return _page("health.html")


@router.get("/stocks", include_in_schema=False)
def stocks() -> FileResponse:
    return _page("stocks.html")


@router.get("/backtest", include_in_schema=False)
def backtest() -> FileResponse:
    return _page("backtest.html")


@router.get("/trades", include_in_schema=False)
def trades() -> FileResponse:
    return _page("trades.html")


@router.get("/strategy", include_in_schema=False)
def strategy() -> FileResponse:
    return _page("strategy.html")


@router.get("/strategy-signals", include_in_schema=False)
def strategy_signals() -> FileResponse:
    return _page("strategy_signals.html")


@router.get("/rankings", include_in_schema=False)
def rankings() -> FileResponse:
    return _page("rankings.html")


@router.get("/research", include_in_schema=False)
def research() -> FileResponse:
    return _page("research.html")


@router.get("/conviction", include_in_schema=False)
def conviction() -> FileResponse:
    return _page("conviction.html")


@router.get("/intraday", include_in_schema=False)
def intraday() -> FileResponse:
    return _page("intraday.html")


@router.get("/market", include_in_schema=False)
def market() -> FileResponse:
    return _page("market.html")


@router.get("/earnings", include_in_schema=False)
def earnings() -> FileResponse:
    return _page("earnings.html")


@router.get("/llm", include_in_schema=False)
def llm() -> FileResponse:
    return _page("llm.html")


@router.get("/gates", include_in_schema=False)
def gates() -> FileResponse:
    return _page("gates.html")
