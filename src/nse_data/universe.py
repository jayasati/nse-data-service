"""Central tracked-universe gate — the single filter every subsystem passes
through.

The system should only spend effort (news, announcements, extraction, LLM,
signals) on symbols worth acting on. This module is the one place that decides
"is this symbol tracked?", backed by the ``tradeable_universe`` table (built by
``scripts/build_tradeable_universe.py`` from candle liquidity + volatility).

    from nse_data import universe
    if not universe.is_tracked(sym):
        continue                          # collectors: skip untracked symbols
    targets = universe.filter_tracked(all_targets)   # fan-out collectors
    head = universe.heading(sym)          # signals: "CORE" / "TRADEABLE" / "VOLATILE"

TWO SAFETY RULES (see plans/UNIVERSE_GATE_PLAN.md):
  1. FAIL-OPEN: if the table is missing/empty, ``is_tracked`` returns True for
     everything (with a warning) — a missing universe must never silently halt
     all collection.
  2. Candle intake is EXEMPT: liquidity/vol (hence the grade) are computed FROM
     candles, so candle collectors stay on the top-1000 candidate pool, NOT this
     gate. Only downstream consumers gate here.
"""
from __future__ import annotations

import logging
import sqlite3
import threading

log = logging.getLogger(__name__)

# Grade values, as written by build_tradeable_universe.py.
GRADE_CORE = "A core"
GRADE_TRADEABLE = "B tradeable"
GRADE_VOLATILE = "C volatile"
GRADE_ILLIQUID = "illiquid"
GRADE_ETF = "etf"

# Membership policy — the symbols that get downstream activity. Tighten to
# {GRADE_CORE, GRADE_TRADEABLE} or {GRADE_CORE} by editing this one set.
TRACKED_GRADES: frozenset[str] = frozenset({GRADE_CORE, GRADE_TRADEABLE, GRADE_VOLATILE})

# Short heading surfaced on signals for the tracked grades.
_HEADING = {GRADE_CORE: "CORE", GRADE_TRADEABLE: "TRADEABLE", GRADE_VOLATILE: "VOLATILE"}

_DEFAULT_DB = "data/nse.db"


class UniverseGate:
    """Loads ``tradeable_universe`` once and answers membership/grade queries.

    Cheap: one query at first use, then a dict lookup. Call ``refresh()`` after a
    rebuild. ``loaded_empty`` records whether the table was missing/empty so the
    gate can fail open.
    """

    def __init__(self, db_path: str = _DEFAULT_DB):
        self.db_path = db_path
        self._grades: dict[str, str] | None = None
        self._lock = threading.Lock()
        self.loaded_empty = False

    # -- loading ------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._grades is not None:
            return
        with self._lock:
            if self._grades is not None:
                return
            self._grades = self._load()

    def _load(self) -> dict[str, str]:
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=30)
            try:
                rows = conn.execute(
                    "SELECT symbol, grade FROM tradeable_universe").fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            # table or DB missing → fail open
            self.loaded_empty = True
            log.warning("universe: tradeable_universe table absent — gate FAILS OPEN "
                        "(every symbol treated as tracked). Run build_tradeable_universe.py.")
            return {}
        if not rows:
            self.loaded_empty = True
            log.warning("universe: tradeable_universe is empty — gate FAILS OPEN.")
            return {}
        self.loaded_empty = False
        return {s.upper(): g for s, g in rows}

    def refresh(self) -> None:
        """Drop the cache so the next call reloads (after a rebuild)."""
        with self._lock:
            self._grades = None
            self.loaded_empty = False

    # -- queries ------------------------------------------------------------
    def grade(self, symbol: str) -> str | None:
        self._ensure_loaded()
        return self._grades.get(symbol.upper()) if self._grades else None

    def is_tracked(self, symbol: str) -> bool:
        """THE gate. True if the symbol should get downstream activity.

        Fails open: when the table is missing/empty every symbol is tracked."""
        self._ensure_loaded()
        if self.loaded_empty:
            return True
        return (self._grades or {}).get(symbol.upper()) in TRACKED_GRADES

    def heading(self, symbol: str) -> str | None:
        """Signal heading: 'CORE' / 'TRADEABLE' / 'VOLATILE' (None if untracked)."""
        return _HEADING.get(self.grade(symbol) or "")

    def tracked_symbols(self) -> set[str]:
        self._ensure_loaded()
        if not self._grades:
            return set()
        return {s for s, g in self._grades.items() if g in TRACKED_GRADES}

    def filter_tracked(self, symbols) -> list[str]:
        """Keep only tracked symbols, preserving order. Fail-open passes all."""
        self._ensure_loaded()
        if self.loaded_empty:
            return list(symbols)
        return [s for s in symbols if (self._grades or {}).get(s.upper()) in TRACKED_GRADES]


# --- module-level singleton + convenience functions ------------------------
_gate: UniverseGate | None = None
_gate_lock = threading.Lock()


def gate(db_path: str = _DEFAULT_DB) -> UniverseGate:
    """The process-wide gate singleton (built on first use)."""
    global _gate
    if _gate is None:
        with _gate_lock:
            if _gate is None:
                _gate = UniverseGate(db_path)
    return _gate


def is_tracked(symbol: str) -> bool:
    return gate().is_tracked(symbol)


def grade(symbol: str) -> str | None:
    return gate().grade(symbol)


def heading(symbol: str) -> str | None:
    return gate().heading(symbol)


def tracked_symbols() -> set[str]:
    return gate().tracked_symbols()


def filter_tracked(symbols) -> list[str]:
    return gate().filter_tracked(symbols)


def refresh() -> None:
    gate().refresh()
