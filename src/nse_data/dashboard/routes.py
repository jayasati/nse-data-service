"""HTML page routes — serve the static dashboard shells.

One route per page; each returns a shell from static/pages/. The shells pull
their data from the api/ surface over fetch(), so there's no logic here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

# The static bundle lives alongside this package; the app mounts it at /static.
STATIC_DIR = Path(__file__).parent / "static"
PAGES_DIR = STATIC_DIR / "pages"

router = APIRouter()


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(PAGES_DIR / "health.html")


@router.get("/stocks", include_in_schema=False)
def stocks() -> FileResponse:
    return FileResponse(PAGES_DIR / "stocks.html")


@router.get("/backtest", include_in_schema=False)
def backtest() -> FileResponse:
    return FileResponse(PAGES_DIR / "backtest.html")


@router.get("/trades", include_in_schema=False)
def trades() -> FileResponse:
    return FileResponse(PAGES_DIR / "trades.html")


@router.get("/market", include_in_schema=False)
def market() -> FileResponse:
    return FileResponse(PAGES_DIR / "market.html")


@router.get("/earnings", include_in_schema=False)
def earnings() -> FileResponse:
    return FileResponse(PAGES_DIR / "earnings.html")
