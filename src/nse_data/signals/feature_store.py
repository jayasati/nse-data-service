"""
Feature store — the ML training archive (task 4.9).

Every time a signal is written, we snapshot ALL the live indicator values that
were true at that instant into `signal_features`, one row per signal. This is
the dataset a future model trains on: "given this VWAP/RSI/trend/volume picture,
did the signal work out?" (the label side is filled later by the nightly
outcome labeler).

Why snapshot instead of joining later: `indicator_live` holds exactly one row
per symbol and is overwritten every minute. The state at 10:32:00 is gone by
10:33:00. If we don't freeze it at fire time it is unrecoverable — hence the
checklist's emphasis that this must run **from day one**.

The feature row is built from:
  * the enrich context (`signals.enrich.read_live_context`) — vwap, vwap_slope,
    rsi_5m, trend_regime, momentum_state, atr_14_daily, ...
  * `volume_ratio` — computed by the detector for the rule, passed through so
    the snapshot matches what fired.
  * `market_regime` — broad-market context (e.g. VIX/Nifty regime); optional,
    None until a publisher exists.
"""

from __future__ import annotations

import sqlite3

# Columns written to signal_features, in table order. `signal_id` is the PK.
_FEATURE_COLUMNS = (
    "signal_id", "symbol", "detected_at",
    "vwap", "vwap_slope", "rsi_5m",
    "trend_regime", "momentum_state", "atr_14_daily",
    "volume_ratio", "market_regime",
)


def snapshot_features(
    conn: sqlite3.Connection,
    signal_id: int,
    symbol: str,
    detected_at: str,
    context: dict,
    *,
    volume_ratio: float | None = None,
    market_regime: str | None = None,
) -> None:
    """Freeze the live indicator picture for `signal_id` into signal_features.

    `context` is an enrich context dict (its keys are pulled by name; absent
    keys snapshot as NULL). INSERT OR REPLACE keyed on signal_id so a re-run of
    the same signal is idempotent.
    """
    values = {
        "signal_id": signal_id,
        "symbol": symbol,
        "detected_at": detected_at,
        "vwap": context.get("vwap"),
        "vwap_slope": context.get("vwap_slope"),
        "rsi_5m": context.get("rsi_5m"),
        "trend_regime": context.get("trend_regime"),
        "momentum_state": context.get("momentum_state"),
        "atr_14_daily": context.get("atr_14_daily"),
        "volume_ratio": volume_ratio,
        "market_regime": market_regime,
    }
    placeholders = ",".join("?" * len(_FEATURE_COLUMNS))
    conn.execute(
        f"INSERT OR REPLACE INTO signal_features ({','.join(_FEATURE_COLUMNS)}) "
        f"VALUES ({placeholders})",
        tuple(values[c] for c in _FEATURE_COLUMNS),
    )
    conn.commit()
