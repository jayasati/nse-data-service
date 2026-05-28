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

from ..dashboard import routes as dashboard_routes
from ..dashboard.routes import STATIC_DIR
from .routes import backtests as backtests_routes
from .routes import health as health_routes
from .routes import stocks as stocks_routes


def create_app() -> FastAPI:
    app = FastAPI(title="NSE Data Service", docs_url="/api/docs")
    app.include_router(dashboard_routes.router)   # human dashboard page shells
    app.include_router(health_routes.router)      # monitoring (/api/health, /api/table)
    app.include_router(stocks_routes.router)      # /api/stocks/*
    app.include_router(backtests_routes.router)   # /api/backtests/*
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
