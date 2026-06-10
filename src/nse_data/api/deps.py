"""FastAPI dependencies — wire a read-only connection through the repository
into a service, and ensure the connection is always closed.

Shared by every route module (api/routes/* and the monitoring routes), so the
DB wiring lives in exactly one place.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException

from ..webcore.db import DatabaseUnavailable, open_ro
from ..webcore.repositories.stock_page import StockPageRepository
from ..webcore.repositories.stocks import StockRepository
from ..webcore.services.stock_page import StockPageService
from ..webcore.services.stocks import StockService


def get_conn() -> Iterator:
    try:
        conn = open_ro()
    except DatabaseUnavailable as e:
        raise HTTPException(503, str(e))
    try:
        yield conn
    finally:
        conn.close()


def get_repo(conn=Depends(get_conn)) -> StockRepository:
    return StockRepository(conn)


def get_service(repo: StockRepository = Depends(get_repo)) -> StockService:
    return StockService(repo)


def get_page_service(conn=Depends(get_conn)) -> StockPageService:
    return StockPageService(StockPageRepository(conn))
