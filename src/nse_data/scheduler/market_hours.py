"""
Market-hours gate for intraday collectors.

NSE equity cash market: 09:15–15:30 IST, Mon–Fri, excluding NSE trading
holidays. This module is the single source of truth — collectors and the
scheduler both consult it.

The holiday list is hand-maintained per the architecture's §15 note
("Holiday calendar: yearly manual refresh"). A future Phase 7 collector
will fetch /api/holiday-master?type=trading and replace this list, but
for now a static set is fine — it's <20 entries per year and we control it.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

# Pre-market session — runs 09:00–09:15 IST.
# Some Phase 7 collectors (pre_open, call_auction) fire here, not after open.
PRE_MARKET_OPEN = time(9, 0)


# NSE trading holidays 2026. Source: nseindia.com/resources/exchange-communication-holidays
# Update yearly. Format: ISO date strings.
# TODO(phase-7): replace with auto-fetch from /api/holiday-master?type=trading
_TRADING_HOLIDAYS: set[date] = {
    date.fromisoformat(d) for d in [
        # 2025
        "2025-02-26",  # Mahashivratri
        "2025-03-14",  # Holi
        "2025-03-31",  # Id-Ul-Fitr
        "2025-04-10",  # Mahavir Jayanti
        "2025-04-14",  # Dr. B.R. Ambedkar Jayanti
        "2025-04-18",  # Good Friday
        "2025-05-01",  # Maharashtra Day
        "2025-06-07",  # Bakri Id
        "2025-07-06",  # Muharram
        "2025-08-15",  # Independence Day
        "2025-08-27",  # Ganesh Chaturthi
        "2025-10-02",  # Mahatma Gandhi Jayanti
        "2025-10-21",  # Diwali
        "2025-10-22",  # Diwali Balipratipada
        "2025-11-05",  # Guru Nanak Jayanti
        "2025-12-25",  # Christmas
        # 2026
        "2026-01-26",  # Republic Day
        # ... (rest of the existing 2026 list)
    ]
}


def now_ist() -> datetime:
    """Current wall-clock time in IST. Centralized so tests can monkeypatch."""
    return datetime.now(IST)


def is_trading_holiday(d: date) -> bool:
    return d in _TRADING_HOLIDAYS


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5   # Saturday=5, Sunday=6


def is_trading_day(d: date) -> bool:
    """A trading day is a weekday that isn't on the holiday list."""
    return not is_weekend(d) and not is_trading_holiday(d)


def is_market_open(at: datetime | None = None) -> bool:
    """
    True only during 09:15–15:30 IST on a trading day.

    The 09:00–09:15 pre-open window returns False here — collectors that
    fire in pre-open use is_pre_market_open() instead. Splitting the two
    keeps the semantics explicit at every call site.
    """
    at = (at or now_ist()).astimezone(IST)
    if not is_trading_day(at.date()):
        return False
    t = at.time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def is_pre_market_open(at: datetime | None = None) -> bool:
    """True during the 09:00–09:15 IST pre-open window on a trading day."""
    at = (at or now_ist()).astimezone(IST)
    if not is_trading_day(at.date()):
        return False
    return PRE_MARKET_OPEN <= at.time() < MARKET_OPEN