-- Signal horizon (Phase 5, swing/intraday split).
--
-- Each signal declares whether it's a swing (hold days–weeks) or intraday
-- (flat by 15:15) setup. Horizon drives scoring weights, the timing gate, the
-- message template, and which Telegram topic it routes to. Set at emission by
-- the rule; backfilled here for existing rows from the known signal types.

ALTER TABLE signals ADD COLUMN horizon TEXT DEFAULT 'swing';

UPDATE signals SET horizon = CASE
    WHEN signal_type IN ('oi_spurt', 'raw_oi_spurts') THEN 'intraday'
    ELSE 'swing'
END;
