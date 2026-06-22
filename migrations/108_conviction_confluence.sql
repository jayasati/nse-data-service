-- Confluence correction: factor-agreement-adjusted conviction + the confirm/contradict read.
ALTER TABLE conviction_daily ADD COLUMN conviction_adj REAL;
ALTER TABLE conviction_daily ADD COLUMN conf_label TEXT;
ALTER TABLE conviction_daily ADD COLUMN conf_agreement INTEGER;
ALTER TABLE conviction_daily ADD COLUMN conf_confirm TEXT;
ALTER TABLE conviction_daily ADD COLUMN conf_against TEXT;
ALTER TABLE conviction_daily ADD COLUMN vol_confirm INTEGER;
