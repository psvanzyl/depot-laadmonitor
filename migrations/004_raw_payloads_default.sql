-- 004_raw_payloads_default.sql — raw_payloads.fetched_at had NOT NULL without a default,
-- so every archived fetch failed. Give it the obvious default.
ALTER TABLE raw_payloads ALTER COLUMN fetched_at SET DEFAULT now();
