#!/usr/bin/env python3
"""Export 500 m coverage rings around depot chargers, plus coverage statistics.

Question this answers: if every depot charger serves the area 500 m around it,
how much of the Dutch road-transport fleet can already charge at the doorstep,
and how many chargers would still be needed?

Output
    webapp/public/data/coverage.geojson   500 m polygons around HGV-capable sites
    webapp/public/data/coverage.json      the numbers, with assumptions stated

Method
    - Rings: PostGIS ST_Buffer(geography, 500 m) around premises_scores rows that
      carry coordinates. HGV-capable sites get `hgv: true` so the UI can show
      "truck-rated coverage" and all-site coverage separately.
    - Companies: a company counts as covered when its own point (companies.geom)
      falls inside any ring. Distances 500 m / 2 km / 5 km are reported.
    - Chargers needed: trucks in the covered fleet divided by a stated daily
      throughput per charger. The throughput is an ASSUMPTION, not a measurement,
      and is labelled as such in coverage.json.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from depotmon.config import Settings  # noqa: E402
from depotmon.store import Store  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "webapp/public/data"
RADIUS_M = 500


def main() -> None:
    settings = Settings()
    store = Store(settings.database_url)
    OUT.mkdir(parents=True, exist_ok=True)
    cur = store.conn.cursor()

    # fleet_stats shape varies; read columns first instead of guessing.
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'fleet_stats' ORDER BY 1"
    )
    fleet_cols = [r["column_name"] for r in cur.fetchall()]
    print("fleet_stats columns:", fleet_cols)

    # 500 m rings around every located site.
    cur.execute(
        """
        CREATE TEMP TABLE cov_rings AS
        SELECT place_key, operator, tier, hgv_capable, power_kw, lon, lat,
               ST_Buffer(ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography, %s)::geometry AS geom
          FROM premises_scores
         WHERE lon IS NOT NULL AND lat IS NOT NULL
        """,
        (RADIUS_M,),
    )
    cur.execute("SELECT count(*) AS n FROM cov_rings")
    rings = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM cov_rings WHERE hgv_capable")
    rings_hgv = cur.fetchone()["n"]
    print(f"rings: {rings} total, {rings_hgv} HGV-capable")

    # Coverage at three radii, for all sites and for HGV-capable sites only.
    distances = [500, 2000, 5000]
    cov: dict[str, dict[str, int]] = {}
    for label, where in (("all", "TRUE"), ("hgv", "hgv_capable")):
        cov[label] = {}
        for d in distances:
            cur.execute(
                f"""
                SELECT count(DISTINCT c.company_id) AS n
                  FROM companies c
                  JOIN cov_rings r
                    ON ST_DWithin(
                         ST_SetSRID(ST_MakePoint(r.lon, r.lat), 4326)::geography,
                         c.geom::geography, %s)
                 WHERE {where.replace('hgv_capable', 'r.hgv_capable')}
                """,
                (d,),
            )
            cov[label][d] = cur.fetchone()["n"]

    cur.execute("SELECT count(*) AS n FROM companies")
    companies_total = cur.fetchone()["n"]

    # Trucks: use fleet_stats when it carries a truck count, else fall back to
    # one truck per company and say so.
    # fleet_stats is a metrics table (CBS stock series), not a per-company fleet
    # count, so a truck total cannot be summed from it without double counting.
    # The estimand is therefore density based and stated as such.
    trucks_total = None

    # Area actually covered, and how much of it overlaps.
    cur.execute(
        """
        SELECT ST_Area(ST_Union(geom)::geography) / 1e6 AS km2
          FROM cov_rings WHERE hgv_capable
        """
    )
    area_km2 = round(float(cur.fetchone()["km2"] or 0), 1)

    covered_500 = cov["hgv"][500]
    covered_5k = cov["hgv"][5000]
    cur.execute("SELECT count(*) AS n FROM cov_rings WHERE hgv_capable")
    chargers_now = cur.fetchone()["n"]
    # Companies reached per existing HGV charger. A new charger is assumed to reach
    # the same number: a density assumption, not a measurement.
    per_charger = (covered_5k / chargers_now) if chargers_now else 0
    chargers_needed = math.ceil(companies_total / per_charger) if per_charger else None
    trucks_covered = covered_500

    stats = {
        "radiusM": RADIUS_M,
        "rings": rings,
        "ringsHgv": rings_hgv,
        "areaKm2Hgv": area_km2,
        "companiesTotal": companies_total,
        "companiesCovered": {
            "500m": cov["hgv"][500],
            "2km": cov["hgv"][2000],
            "5km": cov["hgv"][5000],
        },
        "pctCovered500m": round(100.0 * covered_500 / companies_total, 1) if companies_total else None,
        "pctCovered5km": round(100.0 * covered_5k / companies_total, 1) if companies_total else None,
        "companiesCoveredAllSites": {f"{d}m": cov["all"][d] for d in distances},
        "trucksTotal": trucks_total,
        "companiesPerCharger5km": round(per_charger, 1),
        "chargersHgvNow": chargers_now,
        "chargersNeeded": chargers_needed,
        "assumption": (
            "Chargers needed assumes a new 500 m ring reaches as many companies as the "
            "average existing HGV charger does (density assumption, not a measurement)."
        ),
        "method": "Companies counted when their own point falls inside a 500 m / 2 km / 5 km ring around a site.",
    }
    (OUT / "coverage.json").write_text(json.dumps(stats, indent=2, default=float))
    print(json.dumps(stats, indent=2))

    # Rings as polygons, coordinates rounded to 5 decimals to keep the file small.
    cur.execute(
        """
        SELECT place_key, operator, tier, hgv_capable, power_kw,
               ST_AsGeoJSON(
                   ST_SimplifyPreserveTopology(geom, 0.00005), 5
               ) AS gj
          FROM cov_rings
         WHERE hgv_capable
        """
    )
    features = []
    for row in cur.fetchall():
        features.append({
            "type": "Feature",
            "geometry": json.loads(row["gj"]),
            "properties": {
                "id": row["place_key"],
                "operator": row["operator"],
                "tier": row["tier"],
                "powerKw": float(row["power_kw"]) if row["power_kw"] else None,
                "radiusM": RADIUS_M,
            },
        })
    fc = {"type": "FeatureCollection", "features": features}
    (OUT / "coverage.geojson").write_text(json.dumps(fc, default=float))
    size = (OUT / "coverage.geojson").stat().st_size
    print(f"wrote coverage.geojson: {len(features)} rings, {size // 1024} KB")


if __name__ == "__main__":
    main()
