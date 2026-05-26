"""



Layer 2 base contract — Collector ABC and the five archetypes.

Every NSE source is a Collector subclass of one of:
  - SnapshotCollector   (A) — timestamped inserts, no dedup
  - EventCollector      (B) — fingerprint dedup
  - CsvCollector        (C) — idempotent date-keyed upsert
  - ReferenceCollector  (D) — replace-snapshot with diff
  - FanoutCollector     (E) — one collector, N requests

The base orchestrates: plan() -> session.get_*(req) -> normalize() -> persist().
Per-request exceptions are captured on the RunReport, not raised.
"""

from __future__ import annotations

import json
import time
import traceback
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal, Mapping, Protocol, Sequence


# ============================================================================
# Shared types
# ============================================================================

ResponseType = Literal["json", "text", "bytes"]

# A Row is just a dict. Keeping it as a type alias documents intent without
# forcing every normalize() to wrap output in a class.
Row = dict[str, Any]


@dataclass(frozen=True)
class Request:
    """
    Description of one HTTP call.

    The base class dispatches to session.get_json / get_text / get_bytes
    based on response_type, matching the Layer 1 SessionManager interface.

    Fields:
        path_or_url   - relative path (json/text) or absolute URL (bytes).
                        For bytes: bhavcopy on nsearchives, PDF attachments, etc.
        referer       - sent as Referer header. NSE requires the right one per endpoint.
        params        - query string (json/text only; bytes ignores it).
        response_type - chooses which session method to call.
        meta          - free-form, echoed back to normalize() via the request arg.
                        FanoutCollector uses this to stamp the symbol so normalize
                        knows which call produced which rows. Also used by FakeSession
                        as the fixture-routing key.
    """
    path_or_url: str
    referer: str | None = None
    params: Mapping[str, Any] | None = None
    response_type: ResponseType = "json"
    meta: Mapping[str, Any] = field(default_factory=dict)


class Session(Protocol):
    """
    The Layer 1 contract. SessionManager satisfies this as-is.
    FakeSession (in tests/conftest.py) implements the same three methods
    against captured fixtures.
    """

    def get_json(
        self,
        endpoint_name: str,
        path: str,
        referer: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any: ...

    def get_text(
        self,
        endpoint_name: str,
        path: str,
        referer: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> str: ...

    def get_bytes(
        self,
        endpoint_name: str,
        url: str,
        referer: str | None = None,
    ) -> bytes: ...


@dataclass(frozen=True)
class PersistResult:
    """
    Outcome of persist(). Every archetype reports the same shape, so the
    scheduler and metrics layers don't care which archetype just ran.
    """
    inserted: int = 0
    updated: int = 0
    deduped: int = 0    # rows skipped because PK/fingerprint already present
    removed: int = 0    # rows in DB but absent from this batch (diff/replace)
    unchanged: int = 0  # rows present in both, no field changed

    def __add__(self, other: "PersistResult") -> "PersistResult":
        return PersistResult(
            inserted=self.inserted + other.inserted,
            updated=self.updated + other.updated,
            deduped=self.deduped + other.deduped,
            removed=self.removed + other.removed,
            unchanged=self.unchanged + other.unchanged,
        )


@dataclass
class ErrorRecord:
    """One per-request failure. Carried on the report, not raised."""
    request_url: str
    request_meta: Mapping[str, Any]
    exc_type: str
    message: str
    traceback: str


@dataclass
class RunReport:
    """
    The single object every run() produces. Stable JSON shape — scheduler,
    metrics, and PR descriptions all consume the same to_dict()/to_json().
    """
    collector: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int = 0
    fetched: int = 0     # HTTP calls attempted
    succeeded: int = 0   # returned without raising
    failed: int = 0      # raised (always equals len(errors_during_fetch))
    rows_seen: int = 0
    persist: PersistResult = field(default_factory=PersistResult)
    errors: list[ErrorRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat()
        d["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, sort_keys=True)


# ============================================================================
# Abstract base
# ============================================================================

class Collector(ABC):
    """Abstract base. Subclasses pick an archetype below, not this directly."""

    name: str = ""    # collector identifier (matches endpoints.yaml key, used as
                      # endpoint_name for Layer 1's circuit/rate-limit/metrics)
    table: str = ""   # destination SQLite table

    # ---- Subclass contract ----

    @abstractmethod
    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        """Return the requests this run should issue. 1 for snapshot, N for fan-out."""

    @abstractmethod
    def normalize(self, data: Any, request: Request) -> list[Row]:
        """
        Pure function. Turn one fetched payload into typed dict rows.

        `data` matches the request's response_type:
            json  -> parsed dict/list
            text  -> str
            bytes -> bytes
        """

    @abstractmethod
    def persist(self, db, rows: list[Row]) -> PersistResult:
        """Archetype-specific write. Provided by each archetype subclass."""

    # ---- Orchestration (do not override) ----

    def run(
        self,
        session: Session,
        db,
        context: Mapping[str, Any] | None = None,
    ) -> RunReport:
        """plan -> fetch each -> normalize -> persist. Exceptions captured."""
        if not self.name:
            raise RuntimeError(f"{type(self).__name__} must set `name`")
        if not self.table:
            raise RuntimeError(f"{type(self).__name__} must set `table`")

        started = datetime.now(timezone.utc)
        t0 = time.perf_counter()
        report = RunReport(collector=self.name, started_at=started)

        all_rows: list[Row] = []
        for req in self.plan(context):
            report.fetched += 1
            try:
                data = self._dispatch(session, req)
                rows = self.normalize(data, req)
                report.rows_seen += len(rows)
                all_rows.extend(rows)
                report.succeeded += 1
            except Exception as e:
                report.failed += 1
                report.errors.append(
                    ErrorRecord(
                        request_url=req.path_or_url,
                        request_meta=dict(req.meta),
                        exc_type=type(e).__name__,
                        message=str(e),
                        traceback=traceback.format_exc(),
                    )
                )

        if all_rows:
            try:
                report.persist = self.persist(db, all_rows)
            except Exception as e:
                # Persist failure is collector-level, not per-request.
                report.errors.append(
                    ErrorRecord(
                        request_url="<persist>",
                        request_meta={},
                        exc_type=type(e).__name__,
                        message=str(e),
                        traceback=traceback.format_exc(),
                    )
                )

        report.finished_at = datetime.now(timezone.utc)
        report.duration_ms = int((time.perf_counter() - t0) * 1000)
        return report

    def _dispatch(self, session: Session, req: Request) -> Any:
        """Route to the right SessionManager method based on response_type."""
        params = dict(req.params) if req.params else None
        if req.response_type == "json":
            return session.get_json(self.name, req.path_or_url, req.referer, params)
        if req.response_type == "text":
            return session.get_text(self.name, req.path_or_url, req.referer, params)
        if req.response_type == "bytes":
            return session.get_bytes(self.name, req.path_or_url, req.referer)
        raise ValueError(f"Unknown response_type: {req.response_type}")


# ============================================================================
# The five archetypes
# ============================================================================

class SnapshotCollector(Collector):
    """
    Archetype A — point-in-time snapshots.

    Each row carries an `as_of` that's part of the PK, so re-running the same
    call later inserts NEW rows rather than overwriting. Used by: oi_spurts,
    live_equity, gainers_losers, most_active, most_active_fno, pre_open, ...
    """
    pk_cols: tuple[str, ...] = ()

    def persist(self, db, rows: list[Row]) -> PersistResult:
        if not self.pk_cols:
            raise RuntimeError(f"{type(self).__name__} must set pk_cols")
        from ..storage import models
        return models.upsert_many(db, self.table, rows, self.pk_cols)


class EventCollector(Collector):
    """
    Archetype B — discrete, deduplicated events.

    Each row has a deterministic fingerprint. Re-running yields zero new rows
    if NSE returned the same events; one new row if a new event appeared.

    Optional Redis hot-set: set `dedup_cache` on an instance and persist() will
    skip rows already known to the cache. SQLite remains the source of truth —
    cache loss never causes duplicates, only extra SELECTs.

    Used by: announcements, board_meetings, large_deals, corporate_actions,
    financial_results, primary_market, ...
    """
    fingerprint_col: str = "fingerprint"
    # Optional — set on the instance from main.py after Redis is up.
    # Type is DedupCache | None but kept untyped here to avoid storage import.
    dedup_cache = None

    def fingerprint(self, row: Row) -> str:
        raise NotImplementedError(
            f"{type(self).__name__} must implement fingerprint(row)"
        )

    def persist(self, db, rows: list[Row]) -> PersistResult:
        # Stamp fingerprints first
        for row in rows:
            row.setdefault(self.fingerprint_col, self.fingerprint(row))

        cache_hits = 0
        rows_to_write = rows

        # Fast path: skip rows already in the cache
        if self.dedup_cache is not None:
            fps = [r[self.fingerprint_col] for r in rows]
            known = self.dedup_cache.contains_many(fps)
            if known:
                rows_to_write = [
                    r for r in rows if r[self.fingerprint_col] not in known
                ]
                cache_hits = len(known)

        if not rows_to_write:
            return PersistResult(deduped=cache_hits)

        from ..storage import models
        result = models.insert_ignore(
            db, self.table, rows_to_write, [self.fingerprint_col]
        )

        # Everything that made it through SQLite is durably stored — cache it all.
        # This includes rows insert_ignore deduped against SQLite (they're already
        # in the DB) and rows it just inserted.
        if self.dedup_cache is not None:
            self.dedup_cache.add_many(
                [r[self.fingerprint_col] for r in rows_to_write]
            )

        return PersistResult(
            inserted=result.inserted,
            deduped=result.deduped + cache_hits,
        )

class CsvCollector(Collector):
    """
    Archetype C — date-stamped bulk files (bhavcopy, fii/dii, volatility).

    URL is parameterized by date via url_for_date(d). Re-running for the same
    date is a no-op (same PK -> all rows unchanged). Backfilling is just
    iterating via run_for_date(). Bhavcopy lives on nsearchives.nseindia.com,
    so this defaults response_type to "bytes" (absolute URL, uses get_bytes).
    """
    pk_cols: tuple[str, ...] = ()
    response_type: ResponseType = "bytes"

    def url_for_date(self, d: date) -> str:
        raise NotImplementedError(
            f"{type(self).__name__} must implement url_for_date(d)"
        )

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        target = (context or {}).get("date") or date.today()
        if isinstance(target, str):
            target = date.fromisoformat(target)
        return [
            Request(
                path_or_url=self.url_for_date(target),
                response_type=self.response_type,
                meta={"date": target.isoformat()},
            )
        ]

    def persist(self, db, rows: list[Row]) -> PersistResult:
        if not self.pk_cols:
            raise RuntimeError(f"{type(self).__name__} must set pk_cols")
        from ..storage import models
        return models.upsert_many(db, self.table, rows, self.pk_cols)

    def run_for_date(self, session: Session, db, d: date) -> RunReport:
        """Convenience wrapper for backfill scripts."""
        return self.run(session, db, context={"date": d})


class ReferenceCollector(Collector):
    """
    Archetype D — slow-moving lists where the current snapshot IS the truth.

    Default behaviour is diff_upsert so callers can see what was added,
    removed, updated, unchanged (needed for /blacklist/changes). Set
    replace_strategy='replace_all' when no audit trail is required.
    Used by: surveillance (-> blacklist), universe, fno_list, index_members,
    holiday_master, ...
    """
    key_cols: tuple[str, ...] = ()
    replace_strategy: Literal["diff", "replace_all"] = "diff"

    def persist(self, db, rows: list[Row]) -> PersistResult:
        if not self.key_cols:
            raise RuntimeError(f"{type(self).__name__} must set key_cols")
        from ..storage import models
        if self.replace_strategy == "replace_all":
            return models.replace_all(db, self.table, rows)
        return models.diff_upsert(db, self.table, rows, self.key_cols)


class FanoutCollector(SnapshotCollector):
    """
    Archetype E — one logical collector that issues N HTTP calls.

    Reads its targets (typically from config/universe.yaml) and emits one
    Request per target. Persistence uses SnapshotCollector semantics per call.
    Per-call errors are isolated by the base run() loop — one failing symbol
    does not abort the others. Used by: option_chain over watchlist symbols.
    """

    def targets(self, context: Mapping[str, Any] | None = None) -> Sequence[Any]:
        raise NotImplementedError(
            f"{type(self).__name__} must implement targets(context)"
        )

    def plan_one(self, target: Any) -> Request:
        raise NotImplementedError(
            f"{type(self).__name__} must implement plan_one(target)"
        )

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return [self.plan_one(t) for t in self.targets(context)]