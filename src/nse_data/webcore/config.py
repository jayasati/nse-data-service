"""Shared configuration for the web read-core — paths, table names, constants.

Single place for the magic strings so repositories/services don't hard-code
them. Frontend asset paths live with the dashboard surface, not here.
"""

from __future__ import annotations

DB_PATH = "data/nse.db"
ENDPOINTS_PATH = "config/endpoints.yaml"

# Raw table names.
BHAV = "raw_bhavcopy_cm"            # whole-market daily OHLCV (EOD)
QUOTES = "raw_equity_quotes"        # live 1-min snapshots (broad feed)
INTRADAY = "raw_intraday_candles"   # backfilled broker minute candles
QUOTE_META = "raw_quote_metadata"   # sector / P-E / mcap for ~F&O names
SCREENER = "raw_fundamentals_screener"

LIVE_INDEX = "NIFTY TOTAL MARKET"   # index whose constituents the broad feed polls
LIVE_FRESH_SECS = 300               # a snapshot older than this isn't "live"
IST_OFFSET = 19800                  # +5:30; makes lightweight-charts (UTC) axis read IST

# Ranking SQL expressions per source. Validated against these maps — never
# interpolated from raw user input.
RANK_EOD = {"turnover": "turnover_lacs", "volume": "volume", "delivery": "delivery_qty * close"}
RANK_LIVE = {"turnover": "value", "volume": "volume", "delivery": "value"}
