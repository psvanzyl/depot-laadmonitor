-- TENtec / AFIR recharging infrastructure (EU official reporting data).
--
-- This is a SECOND source beside the OSM and Depotcharge layers. It is the
-- Member State AFIR reporting set, so it carries the TEN-T core-network flags,
-- the corridors, the operator and the connector power per connector.
--
-- Rows are aggregated to ONE ROW PER CHARGING LOCATION, keyed on the AFIR
-- LOCATION_UNIQUE_ID. The source publishes connector rows (1,280,290 of them
-- at the time of writing), and the same location repeats once per connector.
-- Storing connectors would multiply the table for no analytical gain, because
-- everything the map needs is the maximum power, the set of standards and the
-- connector count.
--
-- vehicle_class exists so light-duty data can be EXCLUDED without being
-- absent. A location is HDV when it can physically charge a truck: 350 kW or
-- more, or an MCS connector. Everything else is LDV. A location with a 400 kW
-- CCS bay also serves cars, but a car does not need a truck bay, so the
-- classifier errs towards HDV: the cost of that error is one extra site in the
-- truck view, and the cost of the opposite error is a missing truck charger.

CREATE TABLE IF NOT EXISTS afir_locations (
    location_id     TEXT PRIMARY KEY,        -- AFIR LOCATION_UNIQUE_ID
    name            TEXT,
    operator        TEXT,
    address         TEXT,
    city            TEXT,
    country         TEXT,                    -- ISO-2
    lon             DOUBLE PRECISION,
    lat             DOUBLE PRECISION,
    geom            geometry(Point, 4326),
    max_power_kw    NUMERIC,
    n_connectors    INTEGER,
    standards       TEXT[],                  -- connector standards present
    access          TEXT,                    -- public | semi-public | NULL
    vehicle_class   TEXT NOT NULL,           -- HDV | LDV
    core_network    BOOLEAN,                 -- on the TEN-T core network
    corridors       TEXT,
    routename       TEXT,
    location_type   TEXT,
    source          TEXT NOT NULL DEFAULT 'tentec',
    -- Operability. TENtec publishes NO availability data: the layer has 24
    -- fields and not one of them is a status. The columns exist so a real-time
    -- feed can fill them without a migration, and stay NULL until one does.
    -- A NULL renders as an em dash, never as "available".
    status          TEXT,                    -- free | busy | unknown
    status_at       TIMESTAMPTZ,
    status_source   TEXT,
    raw             JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS afir_locations_geom_idx
    ON afir_locations USING GIST (geom);
CREATE INDEX IF NOT EXISTS afir_locations_country_idx
    ON afir_locations (country);
CREATE INDEX IF NOT EXISTS afir_locations_class_idx
    ON afir_locations (vehicle_class);
CREATE INDEX IF NOT EXISTS afir_locations_power_idx
    ON afir_locations (max_power_kw);
CREATE INDEX IF NOT EXISTS afir_locations_core_idx
    ON afir_locations (core_network) WHERE core_network;
