-- Promoter pledge % (of the promoter's own holding) parsed from the SHP XBRL
-- (parsers/shp_xbrl.py). Populated by the fill/backfill scripts on re-run; NULL until
-- then. Feeds the Risk engine's promoter red-flag.
ALTER TABLE raw_shareholding_quarterly ADD COLUMN promoter_pledge_pct REAL;
