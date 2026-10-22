-- 001_init.sql — depot-laadmonitor schema (PostGIS, EPSG:28992 internally)
-- Idempotent: safe to re-run.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- runs: one row per pipeline step execution
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    run_id      bigserial PRIMARY KEY,
    kind        text NOT NULL,                 -- ingest | resolve | classify | diff | report
    source      text,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL DEFAULT 'running',   -- running | ok | suspect | failed
    records_in  integer NOT NULL DEFAULT 0,
    records_out integer NOT NULL DEFAULT 0,
    note        text,
    error       text
);

-- ---------------------------------------------------------------------------
-- raw_payloads: every fetched document, content-addressed
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_payloads (
    payload_hash  text PRIMARY KEY,
    source        text NOT NULL,
    url           text NOT NULL,
    fetched_at    timestamptz NOT NULL,
    run_id        bigint REFERENCES runs(run_id) ON DELETE SET NULL,
    content_type  text,
    http_status   integer,
    bytes         integer,
    storage_path  text NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_payloads_source_idx ON raw_payloads (source, fetched_at DESC);

-- ---------------------------------------------------------------------------
-- signals: normalised per-source records (staging), one row per source object
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    signal_id       text PRIMARY KEY,            -- '<source>:<source_key>'
    source          text NOT NULL,
    source_key      text NOT NULL,
    kind            text NOT NULL,               -- permit | chargepoint | osm | fleet_stat
    name            text,
    operator        text,
    address         text,
    postcode        text,
    city            text,
    lon             double precision,
    lat             double precision,
    geom            geometry(Point, 28992),
    bag_id          text,
    power_kw        numeric,
    n_points        integer,
    connector_types text[],
    hgv_capable     boolean,
    access          text,
    status_hint     text,
    text_excerpt    text,
    doc_url         text,
    observed_at     date,
    raw_state       jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_hash    text NOT NULL,
    first_seen      timestamptz NOT NULL DEFAULT now(),
    last_seen       timestamptz NOT NULL DEFAULT now(),
    matched_site_id uuid
);
CREATE INDEX IF NOT EXISTS signals_geom_idx ON signals USING gist (geom);
CREATE INDEX IF NOT EXISTS signals_kind_idx ON signals (kind);
CREATE INDEX IF NOT EXISTS signals_site_idx ON signals (matched_site_id);

-- ---------------------------------------------------------------------------
-- sites: one row per physical depot
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sites (
    site_id              uuid PRIMARY KEY,
    name                 text,
    operator             text,
    address              text,
    postcode             text,
    city                 text,
    bag_id               text,
    lon                  double precision,
    lat                  double precision,
    geom                 geometry(Point, 28992) NOT NULL,
    power_kw_announced   numeric,
    power_kw_operational numeric,
    n_points             integer,
    connector_types      text[],
    hgv_capable          boolean NOT NULL DEFAULT false,
    access               text,
    status               text NOT NULL DEFAULT 'unknown',
    confidence           numeric NOT NULL DEFAULT 0,
    province             text,
    municipality         text,
    first_seen           timestamptz NOT NULL DEFAULT now(),
    last_seen            timestamptz NOT NULL DEFAULT now(),
    state_hash           text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sites_geom_idx ON sites USING gist (geom);
CREATE INDEX IF NOT EXISTS sites_status_idx ON sites (status);
CREATE INDEX IF NOT EXISTS sites_province_idx ON sites (province);
CREATE UNIQUE INDEX IF NOT EXISTS sites_bag_uniq ON sites (bag_id) WHERE bag_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- evidence: provenance per site
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id  bigserial PRIMARY KEY,
    site_id      uuid NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
    source       text NOT NULL,
    url          text,
    observed_at  date,
    fetched_at   timestamptz,
    excerpt      text,
    confidence   numeric,
    payload_hash text
);
CREATE INDEX IF NOT EXISTS evidence_site_idx ON evidence (site_id);

-- ---------------------------------------------------------------------------
-- site_snapshots + changes: what the diff is computed against
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS site_snapshots (
    run_id               bigint NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    site_id              uuid NOT NULL,
    status               text,
    confidence           numeric,
    power_kw_announced   numeric,
    power_kw_operational numeric,
    n_points             integer,
    state_hash           text,
    captured_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, site_id)
);

CREATE TABLE IF NOT EXISTS changes (
    change_id   bigserial PRIMARY KEY,
    run_id      bigint NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    site_id     uuid NOT NULL,
    change_type text NOT NULL,                   -- new | changed | removed
    fields      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS changes_run_idx ON changes (run_id);

-- ---------------------------------------------------------------------------
-- operations: source health, alerts, fleet context, LLM cache
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_health (
    source     text NOT NULL,
    day        date NOT NULL,
    ok         boolean NOT NULL,
    records    integer NOT NULL DEFAULT 0,
    deviation  numeric,
    error      text,
    checked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, day)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id   bigserial PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    source     text,
    kind       text NOT NULL,                    -- source_failure | count_deviation | other
    message    text NOT NULL,
    payload    jsonb NOT NULL DEFAULT '{}'::jsonb,
    acknowledged boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS fleet_stats (
    metric   text NOT NULL,                      -- rdw_ze_n3_first_registrations
    period   text NOT NULL,                      -- YYYY-MM
    region   text NOT NULL DEFAULT 'NL',
    value    numeric NOT NULL,
    source   text NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (metric, period, region)
);

CREATE TABLE IF NOT EXISTS llm_cache (
    input_hash  text PRIMARY KEY,
    model       text NOT NULL,
    prompt      text NOT NULL,
    output      text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- internal storage is EPSG:28992; expose EPSG:4326 everywhere outward
CREATE OR REPLACE VIEW sites_wgs84 AS
SELECT site_id, name, operator, address, postcode, city, bag_id,
       power_kw_announced, power_kw_operational, n_points, connector_types,
       hgv_capable, access, status, confidence, province, municipality,
       first_seen, last_seen,
       ST_Y(ST_Transform(geom, 4326)) AS lat,
       ST_X(ST_Transform(geom, 4326)) AS lon
FROM sites;
