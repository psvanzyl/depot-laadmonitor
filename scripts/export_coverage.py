#!/usr/bin/env python3
"""Export 500 m coverage rings around depot chargers + coverage statistics, Europe-wide.

Question this answers: if every depot charger serves the area 500 m around it,
how much of the road-transport fleet can already charge at the doorstep, and
how many chargers would still be needed?

Output
    webapp/public/data/coverage.geojson   500 m polygons around HGV-capable sites
    webapp/public/data/coverage.json      the numbers, per country and overall

Method
    - Rings: PostGIS ST_Buffer(geography, 500 m) around premises_scores rows that
      carry coordinates. HGV-capable sites get `hgv: true` so the UI can show
      "truck-rated coverage" and all-site coverage separately.
    - Companies: a company counts as covered when its own point (companies.geom)
      falls inside any ring. Distances 500 m / 2 km / 5 km are reported.
    - Chargers needed: the fleet in a country divided by the density an existing
      charger in that country already reaches. The density is an ASSUMPTION, not
      a measurement, and is labelled as such in coverage.json.

The per-country table is the point of this file. A Europe-wide average hides
that Germany has 40x the chargers of Portugal but 6x the companies, and that
the ratio is what decides whether a fleet needs 4 chargers or 4,000.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from depotmon.config import Settings  # noqa: E402
from depotmon.store import Store  # noqa: E402
from depotmon.ingest.osm_eu import ISO3_TO_ISO2  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "webapp/public/data"
RADIUS_M = 500

# Radii exported for the map. 500 m is the headline figure the fleet estimate
# is built on; the others let the reader zoom out, the way the pakketpuntenviewer
# lets you pick the radius of the circle you draw around a point.
RING_RADII = [500, 1000, 2000, 5000]

# The ISO-3 -> ISO-2 map lives in osm_eu.py, next to its reverse.


def _norm_country(raw) -> str:
    """Normalise a country code to ISO-2, whatever shape it arrived in."""
    code = (raw or "").strip().upper()
    if not code:
        return ""
    if code in ISO3_TO_ISO2:
        return ISO3_TO_ISO2[code]
    if len(code) == 2:
        return code
    return ""


def main() -> None:
    settings = Settings()
    store = Store(settings.database_url)
    OUT.mkdir(parents=True, exist_ok=True)
    cur = store.conn.cursor()

    # A site belongs to the country of the signal that produced it. The
    # premises table does not carry a country, so it is resolved here with the
    # same rules the exporter uses for the map features:
    #   1. the signal's own raw_state country
    #   2. the Dutch province (province => NL)
    #   3. the country whose box contains the point
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
    from depotmon.ingest.osm_eu import country_of  # polygon-backed

    cur.execute(
        """
        CREATE TEMP TABLE cov_rings AS
        SELECT p.place_key, p.operator, p.tier, p.hgv_capable, p.power_kw, p.lon, p.lat,
               -- The signals.geom column is in RD New (28992), not WGS84, so the
               -- distance test runs on the lon/lat columns, not on geom.
               COALESCE(
                   (SELECT s.raw_state->>'country' FROM signals s
                     WHERE s.lon IS NOT NULL AND s.lat IS NOT NULL
                       AND ST_DWithin(
                             ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326)::geography,
                             ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326)::geography,
                             300)
                       AND s.raw_state->>'country' IS NOT NULL
                     ORDER BY ST_Distance(
                             ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326)::geography,
                             ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326)::geography)
                     LIMIT 1),
                   NULL
               ) AS country_raw,
               ST_Buffer(ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326)::geography, %s)::geometry AS geom
          FROM premises_scores p
         WHERE p.lon IS NOT NULL AND p.lat IS NOT NULL
        """,
        (RADIUS_M,),
    )
    cur.execute("SELECT count(*) AS n FROM cov_rings")
    rings = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM cov_rings WHERE hgv_capable")
    rings_hgv = cur.fetchone()["n"]
    print(f"rings: {rings} total, {rings_hgv} HGV-capable")

    # Fill the country for rings with no signal country, using the box test.
    cur.execute("SELECT place_key, lon, lat, country_raw FROM cov_rings")
    for row in cur.fetchall():
        code = _norm_country(row["country_raw"])
        if not code:
            code = country_of(float(row["lon"]), float(row["lat"]))
        if not code:
            code = "NL" if (3.2 <= float(row["lon"]) <= 7.3
                            and 50.7 <= float(row["lat"]) <= 53.6) else ""
        if code != (row["country_raw"] or ""):
            cur.execute("UPDATE cov_rings SET country_raw = %s WHERE place_key = %s",
                        (code, row["place_key"]))

    # Coverage at three radii, overall and per country.
    distances = [500, 2000, 5000]

    def _covered(where: str, d: int) -> int:
        cur.execute(
            f"""
            SELECT count(DISTINCT c.company_id) AS n
              FROM companies c
              JOIN cov_rings r
                ON ST_DWithin(
                     ST_SetSRID(ST_MakePoint(r.lon, r.lat), 4326)::geography,
                     c.geom::geography, %s)
             WHERE {where}
            """,
            (d,),
        )
        return cur.fetchone()["n"]

    cov_all = {d: _covered("TRUE", d) for d in distances}
    cov_hgv = {d: _covered("r.hgv_capable", d) for d in distances}

    cur.execute("SELECT count(*) AS n FROM companies")
    companies_total = cur.fetchone()["n"]

    # Per-country: companies, covered companies and chargers, so the fleet
    # estimate can be computed per country instead of one Europe-wide number.
    cur.execute(
        """
        SELECT COALESCE(NULLIF(c.country, ''), 'NL') AS cc,
               count(DISTINCT c.company_id) AS companies
          FROM companies c
         WHERE c.geom IS NOT NULL
         GROUP BY 1
        """
    )
    companies_by_country = {r["cc"]: r["companies"] for r in cur.fetchall()}

    cur.execute(
        """
        SELECT COALESCE(NULLIF(country_raw, ''), 'NL') AS cc,
               count(*) AS chargers,
               count(*) FILTER (WHERE hgv_capable) AS chargers_hgv
          FROM cov_rings
         GROUP BY 1
        """
    )
    chargers_by_country = {
        r["cc"]: {"chargers": r["chargers"], "chargersHgv": r["chargers_hgv"]}
        for r in cur.fetchall()
    }

    cur.execute(
        """
        SELECT COALESCE(NULLIF(r.country_raw, ''), 'NL') AS cc,
               count(DISTINCT c.company_id) AS covered500,
               count(DISTINCT c.company_id) FILTER (
                   WHERE ST_DWithin(
                       ST_SetSRID(ST_MakePoint(r.lon, r.lat), 4326)::geography,
                       c.geom::geography, 5000)) AS covered5k
          FROM cov_rings r
          JOIN companies c
            ON ST_DWithin(
                 ST_SetSRID(ST_MakePoint(r.lon, r.lat), 4326)::geography,
                 c.geom::geography, 5000)
         WHERE r.hgv_capable
         GROUP BY 1
        """
    )
    covered_by_country = {
        r["cc"]: {"500m": r["covered500"], "5km": r["covered5k"]}
        for r in cur.fetchall()
    }

    country_rows = []
    for cc in sorted(set(companies_by_country) | set(chargers_by_country)):
        comp = companies_by_country.get(cc, 0)
        ch = chargers_by_country.get(cc, {})
        cov = covered_by_country.get(cc, {})
        chargers_hgv = ch.get("chargersHgv", 0)
        covered_5k = cov.get("5km", 0)
        # Density assumption: a new charger reaches as many companies as the
        # average existing HGV charger in that country already reaches.
        per_charger = (covered_5k / chargers_hgv) if chargers_hgv else 0.0
        needed = math.ceil(comp / per_charger) if per_charger > 0 else None
        country_rows.append({
            "country": cc,
            "companies": comp,
            "chargers": ch.get("chargers", 0),
            "chargersHgv": chargers_hgv,
            "covered500m": cov.get("500m", 0),
            "covered5km": covered_5k,
            "pctCovered500m": round(100.0 * cov.get("500m", 0) / comp, 1) if comp else None,
            "pctCovered5km": round(100.0 * covered_5k / comp, 1) if comp else None,
            "companiesPerCharger5km": round(per_charger, 1),
            "chargersNeeded": needed,
        })
    country_rows.sort(key=lambda r: -r["chargers"])

    # Area actually covered, and how much of it overlaps.
    cur.execute(
        """
        SELECT ST_Area(ST_Union(geom)::geography) / 1e6 AS km2
          FROM cov_rings WHERE hgv_capable
        """
    )
    area_km2 = round(float(cur.fetchone()["km2"] or 0), 1)

    covered_500 = cov_hgv[500]
    covered_5k = cov_hgv[5000]
    chargers_now = rings_hgv
    per_charger = (covered_5k / chargers_now) if chargers_now else 0
    chargers_needed = math.ceil(companies_total / per_charger) if per_charger else None

    stats = {
        "radiusM": RADIUS_M,
        "rings": rings,
        "ringsHgv": rings_hgv,
        "areaKm2Hgv": area_km2,
        "companiesTotal": companies_total,
        "companiesCovered": {
            "500m": cov_hgv[500],
            "2km": cov_hgv[2000],
            "5km": cov_hgv[5000],
        },
        "pctCovered500m": round(100.0 * covered_500 / companies_total, 1) if companies_total else None,
        "pctCovered5km": round(100.0 * covered_5k / companies_total, 1) if companies_total else None,
        "companiesCoveredAllSites": {f"{d}m": cov_all[d] for d in distances},
        "trucksTotal": None,
        "companiesPerCharger5km": round(per_charger, 1),
        "chargersHgvNow": chargers_now,
        "chargersNeeded": chargers_needed,
        "countries": country_rows,
        "assumption": (
            "Chargers needed assumes a new 500 m ring reaches as many companies as the "
            "average existing HGV charger does (density assumption, not a measurement)."
        ),
        "method": (
            "Companies counted when their own point falls inside a 500 m / 2 km / 5 km ring "
            "around a site. Per country, the chargers needed are the companies in that "
            "country divided by the density its existing HGV chargers already reach."
        ),
    }
    (OUT / "coverage.json").write_text(json.dumps(stats, indent=2, default=float))
    print(json.dumps({k: v for k, v in stats.items() if k != "countries"}, indent=2, default=float))
    print(f"countries with data: {len(country_rows)}")
    for r in country_rows[:12]:
        print(f"  {r['country']}: {r['chargersHgv']} chargers, {r['companies']} companies, "
              f"{r['pctCovered500m']}% at 500 m, need {r['chargersNeeded']}")

    # Rings as polygons, one set per radius, so the map can switch radius
    # without a data reload. Coordinates rounded to 5 decimals to keep the file
    # small; 5 decimals is about 1 m, well below the precision of a charger
    # position.
    features = []
    for radius in RING_RADII:
        cur.execute(
            """
            SELECT place_key, operator, tier, hgv_capable, power_kw,
                   COALESCE(NULLIF(country_raw, ''), '') AS country,
                   ST_AsGeoJSON(
                       ST_SimplifyPreserveTopology(
                           ST_Buffer(
                               ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography,
                               %s)::geometry, 0.00005), 5
                   ) AS gj
              FROM cov_rings
             WHERE hgv_capable
            """,
            (radius,),
        )
        for row in cur.fetchall():
            features.append({
                "type": "Feature",
                "geometry": json.loads(row["gj"]),
                "properties": {
                    "id": row["place_key"],
                    "operator": row["operator"],
                    "tier": row["tier"],
                    "powerKw": float(row["power_kw"]) if row["power_kw"] else None,
                    "radiusM": radius,
                    "country": row["country"],
                },
            })
    fc = {"type": "FeatureCollection", "features": features}
    (OUT / "coverage.geojson").write_text(json.dumps(fc, default=float))
    size = (OUT / "coverage.geojson").stat().st_size
    print(f"wrote coverage.geojson: {len(features)} rings across {len(RING_RADII)} radii, "
          f"{size // 1024} KB")


if __name__ == "__main__":
    main()
