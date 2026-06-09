-- Signal direction (Phase 5 — Event Intelligence, E3).
--
-- Every Phase-1 signal was implicitly long. The earnings-reaction signal can be
-- SHORT (a bad result that breaks down), so signals and their paper trades need
-- an explicit direction. Defaults to 'long' so all existing rows/logic are
-- unchanged.

ALTER TABLE signals      ADD COLUMN direction TEXT DEFAULT 'long';
ALTER TABLE paper_trades ADD COLUMN direction TEXT DEFAULT 'long';
