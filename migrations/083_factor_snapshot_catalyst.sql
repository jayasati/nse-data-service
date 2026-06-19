-- Catalyst (Order Impact Ratio) score into the feature store so the Buy Decision
-- card can read it from the daily snapshot. Sparse/event-driven (only names with a
-- recent extractable order), so mostly NULL — that's expected.
ALTER TABLE factor_snapshot ADD COLUMN catalyst REAL;
