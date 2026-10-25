-- One charger table for the coverage calculation, spanning both sources.
--
-- The coverage counted only the OSM and Depotcharge sites (premises_scores).
-- The 6,085 AFIR truck locations were drawn on the map but invisible to the
-- calculation, so a company 200 m from an official EU charging pool counted as
-- uncovered. This view makes the two sources one table, with a `source` column
-- so the split stays auditable.
--
-- Only HDV AFIR locations are included. A 22 kW car post does not charge a
-- truck, and counting light duty here would inflate truck coverage with
-- infrastructure that cannot serve the fleet.
--
-- `tier` is not a real score for the AFIR rows: they are unscored official
-- reporting. Core-network sites get A and the rest B so nothing sorts to C
-- (lead) by accident. The coverage filter does not read tier, so this only
-- affects the pair table's attribute column.

CREATE OR REPLACE VIEW coverage_chargers AS
WITH dep AS (
    SELECT 'osm:' || p.place_key                      AS place_key,
           p.operator,
           p.tier,
           p.hgv_capable,
           p.power_kw,
           p.lon,
           p.lat,
           COALESCE(NULLIF(p.access_class, ''), 'unknown') AS access_class,
           (SELECT s.raw_state->>'country'
              FROM signals s
             WHERE s.lon IS NOT NULL AND s.lat IS NOT NULL
               AND s.raw_state->>'country' IS NOT NULL
               AND ST_DWithin(
                     ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326)::geography,
                     ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326)::geography,
                     300)
             ORDER BY ST_Distance(
                     ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326)::geography,
                     ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326)::geography)
             LIMIT 1)                                 AS country_raw,
           'osm'::text                                    AS source
      FROM premises_scores p
     WHERE p.lon IS NOT NULL AND p.lat IS NOT NULL
),
afir AS (
    SELECT 'afir:' || a.location_id                   AS place_key,
           a.operator,
           CASE WHEN a.core_network THEN 'A' ELSE 'B' END AS tier,
           TRUE                                       AS hgv_capable,
           a.max_power_kw                             AS power_kw,
           a.lon,
           a.lat,
           CASE a.access
                WHEN 'public' THEN 'public'
                WHEN 'semi-public' THEN 'semi_public'
                WHEN 'private' THEN 'private'
                ELSE 'unknown'
           END                                        AS access_class,
           a.country                                  AS country_raw,
           'tentec'::text                             AS source
      FROM afir_locations a
     WHERE a.vehicle_class = 'HDV'
       AND a.lon IS NOT NULL AND a.lat IS NOT NULL
)
SELECT * FROM dep
UNION ALL
SELECT * FROM afir;
