-- Short support: each paper_book position carries its side (default 'long' for back-compat).
ALTER TABLE paper_book ADD COLUMN direction TEXT DEFAULT 'long';
