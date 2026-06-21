"""FastAPI app for the NSE data service — machine API + human dashboard.

    uvicorn nse_data.api.server:app --port 8000
    # or
    python -m nse_data.api.server

Composition root only: wires the route groups and mounts the static frontend.
The real work lives in `webcore` (the shared, framework-free read-core) behind
two thin surfaces, per docs/adr/0001-web-service-structure.md:

    webcore/            config, db, repositories, services, transforms, introspect
    api/routes/*        machine JSON API (stocks + monitoring; Layer 7 routes land here)
    dashboard/          human dashboard (page shells + static bundle)
    ops/                framework-free observability domain (health, metrics, replay)

The DB is opened read-only, per request, so the live collector (writing under
WAL) is never blocked or disturbed.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope


class RevalidatingStatic(StaticFiles):
    """StaticFiles that asks the browser to revalidate every asset.

    The bundle is unversioned (no ?v=<hash> on the script/CSS tags), so without
    this a browser happily serves a stale cached stocks.js after a deploy —
    which silently breaks the dashboard against a changed API until a manual
    hard refresh. `no-cache` doesn't mean "don't cache": it means "cache, but
    revalidate before use" — the browser sends If-None-Match/If-Modified-Since
    and StaticFiles answers 304 when unchanged (cheap), or 200 with the new file
    right after a deploy. So pages stay fast AND always run current code.
    """

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response

from ..dashboard import routes as dashboard_routes
from ..dashboard.routes import STATIC_DIR
from .routes import backtests as backtests_routes
from .routes import earnings as earnings_routes
from .routes import health as health_routes
from .routes import llm as llm_routes
from .routes import market as market_routes
from .routes import stocks as stocks_routes
from .routes import strategy_analytics as strategy_analytics_routes
from .routes import trades as trades_routes


def create_app() -> FastAPI:
    app = FastAPI(title="NSE Data Service", docs_url="/api/docs")
    app.include_router(dashboard_routes.router)   # human dashboard page shells
    app.include_router(health_routes.router)      # monitoring (/api/health, /api/table)
    app.include_router(stocks_routes.router)      # /api/stocks/*
    app.include_router(backtests_routes.router)   # /api/backtests/*
    app.include_router(trades_routes.router)       # /api/trades/* (live paper trades)
    app.include_router(market_routes.router)        # /api/market/* (regime + sector radar)
    app.include_router(earnings_routes.router)      # /api/earnings/* (reaction odds + setups)
    app.include_router(llm_routes.router)            # /api/llm/* (spend tracking)
    app.include_router(strategy_analytics_routes.router)  # /api/strategy-analytics/* (backtest study)
    app.mount("/static", RevalidatingStatic(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
