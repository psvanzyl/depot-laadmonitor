-- 005: company base layer (transport/shipping premises in NL)
CREATE TABLE IF NOT EXISTS companies (
    company_id   BIGSERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    name_norm    TEXT NOT NULL,
    kind         TEXT,                    -- carrier | shipper | unknown
    address      TEXT,
    postcode     TEXT,
    city         TEXT,
    geom         geometry(Point, 4326),
    source       TEXT NOT NULL,           -- osm | kvk
    source_url   TEXT,
    external_id  TEXT NOT NULL,
    tags         JSONB DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ DEFAULT now(),
    last_seen    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS companies_geom_idx ON companies USING GIST (geom);
CREATE INDEX IF NOT EXISTS companies_norm_idx ON companies (name_norm);
CREATE INDEX IF NOT EXISTS companies_city_idx ON companies (lower(city));

-- how a depot row got onto the map: its own yard, or only a city approximation
ALTER TABLE premises_scores ADD COLUMN IF NOT EXISTS lon DOUBLE PRECISION;
ALTER TABLE premises_scores ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION;
ALTER TABLE premises_scores ADD COLUMN IF NOT EXISTS location_basis TEXT;
ALTER TABLE premises_scores ADD COLUMN IF NOT EXISTS company_id BIGINT;
