-- Call/put OI walls on the options metrics (resistance/support strikes for intraday bias).
ALTER TABLE options_metrics ADD COLUMN call_wall REAL;
ALTER TABLE options_metrics ADD COLUMN put_wall REAL;
