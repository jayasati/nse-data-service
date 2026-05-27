"""Pure candle transforms — no DB, no framework. Easy to unit-test.

All operate on plain candle dicts ({time, open, high, low, close, volume}) or
the raw snapshot tuples from the live feed. `time` is an IST-offset epoch (or a
'YYYY-MM-DD' string for daily/weekly) — these functions don't care which.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date

from .config import IST_OFFSET


def ist_day(epoch: int) -> int:
    """IST calendar-day bucket for an already-IST-offset epoch (dedup key)."""
    return epoch // 86400


def intraday_line(rows: list) -> list[dict]:
    """Live 1-min snapshots (as_of, price, cum_volume) -> one point per minute.
    Volume is the per-step delta of the day's cumulative volume; the first
    sample's cumulative-from-open total is shown as 0 so it doesn't dwarf the
    per-minute bars."""
    out, prev = [], None
    for as_of, price, vol in rows:
        v = (vol - prev) if (prev is not None and vol is not None and vol >= prev) else 0
        prev = vol
        out.append({"time": as_of + IST_OFFSET, "value": price, "volume": v})
    return out


def line_to_candles(points: list[dict]) -> list[dict]:
    """Flatten 1-min line points into degenerate OHLC candles (O=H=L=C=price)."""
    return [{"time": p["time"], "open": p["value"], "high": p["value"],
             "low": p["value"], "close": p["value"], "volume": p["volume"]}
            for p in points]


def merge_candles(hist: list[dict], live: list[dict]) -> list[dict]:
    """Backfilled history + today's live-derived candles, oldest-first. Days
    covered by `hist` win — drop live points for any day already backfilled."""
    if not hist:
        return live
    hist_days = {ist_day(c["time"]) for c in hist}
    merged = list(hist) + [c for c in live if ist_day(c["time"]) not in hist_days]
    merged.sort(key=lambda c: c["time"])
    return merged


def resample_intraday(candles: list[dict], bucket: int) -> list[dict]:
    """Aggregate 1-minute candles into `bucket`-second OHLC bars. IST_OFFSET is a
    multiple of 300, so 5-min buckets align to the IST clock."""
    out: "OrderedDict[int, dict]" = OrderedDict()
    for c in candles:
        b = (c["time"] // bucket) * bucket
        if b not in out:
            out[b] = {"time": b, "open": c["open"], "high": c["high"],
                      "low": c["low"], "close": c["close"], "volume": c["volume"] or 0}
        else:
            o = out[b]
            o["high"] = max(o["high"], c["high"])
            o["low"] = min(o["low"], c["low"])
            o["close"] = c["close"]
            o["volume"] += c["volume"] or 0
    return list(out.values())


def resample_weekly(daily: list[dict]) -> list[dict]:
    """Group daily candles into ISO-week OHLC bars (first session opens the bar)."""
    weeks: "OrderedDict[tuple, dict]" = OrderedDict()
    for d in daily:
        y, w, _ = date.fromisoformat(d["time"]).isocalendar()
        key = (y, w)
        if key not in weeks:
            weeks[key] = dict(d)
        else:
            bar = weeks[key]
            bar["high"] = max(bar["high"], d["high"])
            bar["low"] = min(bar["low"], d["low"])
            bar["close"] = d["close"]
            bar["volume"] = (bar["volume"] or 0) + (d["volume"] or 0)
    return list(weeks.values())
