-- Pre-event risk + psychological state on the live snapshot (Weeks 18.2 / 19.1).
--
-- New indicator_live columns, written by three different cadences:
--   * events/pre_event_risk.py (nightly 20:20)  — pre_event_run_5d/10d,
--     days_to_event, pre_event_state for symbols with a result due ≤10 days.
--   * psychology/state_classifier.py (every 5 min, market hours) — refreshes
--     the same measurements intraday plus consecutive_up/down_days and the
--     8-state psych_state tag.
--   * indicators/live_snapshot.py (every minute) — does NOT touch these
--     columns: its write is an UPSERT over its own column set, so the slower
--     writers' values survive the minute rewrite.
--
-- pre_event_state ∈ BUY_RUMOR_IN_PLAY / MILD_ANTICIPATION / NORMAL / MILD_FEAR
--                   / FEAR_PRICED / SELL_RUMOR_IN_PLAY   (checklist 18.2 bands;
--                   MILD_FEAR fills the −3..−8% gap the checklist leaves open)
-- psych_state ∈ FOMO_EUPHORIA / BUY_RUMOR / NEUTRAL_TRENDING / SELL_NEWS /
--               FEAR_BUILDING / CAPITULATION / RELIEF_BOUNCE / DEAD_CAT_BOUNCE

ALTER TABLE indicator_live ADD COLUMN pre_event_run_5d REAL;
ALTER TABLE indicator_live ADD COLUMN pre_event_run_10d REAL;
ALTER TABLE indicator_live ADD COLUMN days_to_event INTEGER;
ALTER TABLE indicator_live ADD COLUMN pre_event_state TEXT;
ALTER TABLE indicator_live ADD COLUMN consecutive_up_days INTEGER;
ALTER TABLE indicator_live ADD COLUMN consecutive_down_days INTEGER;
ALTER TABLE indicator_live ADD COLUMN psych_state TEXT;
