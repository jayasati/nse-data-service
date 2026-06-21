"""Weekly-ranking service — shapes weekly_ranking rows for the UI (week list + per-week table)."""
from __future__ import annotations

import json

from ..errors import NotFound, Unavailable
from ..repositories.rankings import RankingsRepository


class RankingsService:
    def __init__(self, repo: RankingsRepository):
        self.repo = repo

    def _ready(self):
        if not self.repo.tables_ready():
            raise Unavailable("weekly_ranking not present (migration 097 not applied / no run yet)")

    def weeks(self) -> dict:
        self._ready()
        rows = self.repo.list_weeks()
        return {"count": len(rows), "latest": rows[0]["week_date"] if rows else None,
                "weeks": [dict(r) for r in rows]}

    def week(self, week_date: str, *, selected_only: bool = False) -> dict:
        self._ready()
        rows = self.repo.week_rows(week_date, selected_only=selected_only)
        if not rows:
            raise NotFound(f"no ranking for week {week_date}")
        return {"week": week_date, "count": len(rows), "rows": [self._row(r) for r in rows]}

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["flags"] = json.loads(d["flags"]) if d.get("flags") else []
        return d
