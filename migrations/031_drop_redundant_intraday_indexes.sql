-- Drop the manual (symbol, ts DESC) indexes on the intraday indicator
-- tables. The PK auto-index on (symbol, ts) covers the same access pattern —
-- SQLite walks it backwards for `ORDER BY ts DESC LIMIT N` at negligible
-- cost — so these manual duplicates only burn disk (~150 MB each at the
-- current ~3.9M-row scale) and slow writes (one fewer index to update).
--
-- The (ts DESC) cross-symbol index stays — it's the only path for the
-- retention sweep (`WHERE ts < cutoff`) and for future signal queries that
-- scan all symbols in a time window.
--
-- After this migration the data/nse.db file does NOT shrink automatically —
-- SQLite reuses the freed pages for future writes. Run VACUUM to reclaim
-- disk if needed:  sqlite3 data/nse.db "VACUUM;"

DROP INDEX IF EXISTS idx_indicator_rsi_5m_symbol_ts;
DROP INDEX IF EXISTS idx_indicator_macd_5m_symbol_ts;
