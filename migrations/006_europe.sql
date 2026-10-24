-- 006: Europe-wide layers
--
-- The company base layer and the OSM charge-point layer both need to know
-- which country a row belongs to, so the coverage analysis can be reported
-- per country instead of one Europe-wide average that hides the differences.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS country TEXT;

CREATE INDEX IF NOT EXISTS companies_country_idx ON companies (country);

-- Signals from the Europe-wide OSM ingest carry their country in raw_state.
-- A generated column lets the exporter and the coverage analysis filter on
-- it without parsing JSONB in every query.
ALTER TABLE signals
  ADD COLUMN IF NOT EXISTS country TEXT
  GENERATED ALWAYS AS (raw_state ->> 'country') STORED;

CREATE INDEX IF NOT EXISTS signals_country_idx ON signals (country);

-- The scored sites need a country too, so the map and the tables can group
-- by it. It is resolved from the source signal, not guessed.
ALTER TABLE premises_scores ADD COLUMN IF NOT EXISTS country TEXT;

CREATE INDEX IF NOT EXISTS premises_scores_country_idx ON premises_scores (country);
