-- raw_announcements.broadcast_dt is an NSE-format STRING ("21-May-2026 19:40:44"),
-- so ORDER BY broadcast_dt sorts LEXICALLY — "31-Jan" outranks "01-Apr" — making
-- every "latest N announcements" query (signal detection, psychology, extraction,
-- UI) return the wrong rows. Add a parsed epoch (IST) column to sort correctly;
-- backfilled by scripts/backfill_broadcast_epoch.py and populated by the collector.

ALTER TABLE raw_announcements ADD COLUMN broadcast_epoch INTEGER;
CREATE INDEX IF NOT EXISTS ix_ann_symbol_epoch ON raw_announcements(symbol, broadcast_epoch);
