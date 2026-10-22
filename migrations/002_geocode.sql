-- 002_geocode.sql — geocoding cache and administrative attributes on signals

CREATE TABLE IF NOT EXISTS geo_cache (
    input_hash   text PRIMARY KEY,          -- sha256 of the normalised query
    query        text NOT NULL,
    ok           boolean NOT NULL,
    lon          double precision,
    lat          double precision,
    precision    text,                      -- address | postcode | place
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    fetched_at   timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE signals ADD COLUMN IF NOT EXISTS province text;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS municipality text;

CREATE INDEX IF NOT EXISTS signals_province_idx ON signals (province);
