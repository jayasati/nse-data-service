-- LLM desk-analyst note: one grounded synthesis per day over the structured signal tables.
-- Analysis product (the "be informed" half), NOT a trade signal — never auto-traded / auto-scored.
CREATE TABLE IF NOT EXISTS desk_notes (
    note_date    TEXT PRIMARY KEY,
    note         TEXT,
    model        TEXT,
    cost_usd     REAL,
    n_signals    INTEGER,        -- how many structured signals fed the note (thin-data guard)
    created_at   TEXT DEFAULT (datetime('now'))
);
