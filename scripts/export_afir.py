"""Publish the TENtec / AFIR charging layer for the web map.

Two files, because the two vehicle classes are not the same size or the same
purpose:

    afir_hdv.geojson   full detail, a few thousand points, loads with the page
    afir_ldv.json      compact arrays, hundreds of thousands of points, loaded
                       only when the light-duty toggle is turned on

Light duty is the bulk of the AFIR data (roughly 95% of locations) and would
swamp a truck map, so it must never be part of the first paint. It is still
published in full, because the request was explicit: load it in, and let the
filter take it out.

The compact form is a flat array per row, not GeoJSON, because GeoJSON repeats
every property name once per feature. [lon, lat, kw, countryIndex, connectors]
is about a quarter of the bytes, and the browser can turn it into a GeoJSON
source in a single pass when the toggle flips.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg
from psycopg.rows import dict_row

from depotmon.config import Settings

OUT = Path(__file__).resolve().parents[1] / "webapp/public/data"


def fetch(conn, vehicle_class: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT location_id, name, operator, city, country, lon, lat,
                   max_power_kw, n_connectors, standards, access,
                   core_network, corridors, routename, location_type
            FROM afir_locations
            WHERE vehicle_class = %s
              AND lon IS NOT NULL AND lat IS NOT NULL
            """,
            (vehicle_class,),
        )
        return cur.fetchall()


def hdv_geojson(rows: list[dict]) -> dict:
    feats = []
    for r in rows:
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(r["lon"], 5), round(r["lat"], 5)]},
            "properties": {
                "id": r["location_id"],
                "name": r["name"],
                "operator": r["operator"],
                "city": r["city"],
                "cc": r["country"],
                "kw": float(r["max_power_kw"] or 0),
                "nc": r["n_connectors"],
                "st": r["standards"] or [],
                "access": r["access"],
                "core": bool(r["core_network"]),
                "cor": r["corridors"],
                "rt": r["routename"],
                "type": r["location_type"],
                # Operability is not published by TENtec. NULL stays NULL so the
                # UI can say "unknown" instead of inventing "available".
                "status": None,
            },
        })
    return {"type": "FeatureCollection", "features": feats}


def ldv_compact(rows: list[dict]) -> dict:
    codes = sorted({r["country"] for r in rows if r["country"]})
    idx = {c: i for i, c in enumerate(codes)}
    out = []
    for r in rows:
        out.append([
            round(r["lon"], 4),
            round(r["lat"], 4),
            int(round(float(r["max_power_kw"] or 0))),
            idx.get(r["country"], -1),
            r["n_connectors"],
        ])
    return {
        "meta": {
            "class": "LDV",
            "count": len(out),
            "countries": codes,
            "fields": ["lon", "lat", "kw", "countryIndex", "connectors"],
        },
        "rows": out,
    }


def summary(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT vehicle_class,
                   count(*) AS n,
                   count(*) FILTER (WHERE core_network) AS on_core,
                   count(*) FILTER (WHERE status IS NOT NULL) AS with_status,
                   max(max_power_kw) AS top_kw
            FROM afir_locations GROUP BY 1 ORDER BY 1
        """)
        by_class = {r["vehicle_class"]: {
            "locations": r["n"],
            "onCoreNetwork": r["on_core"],
            "withOperability": r["with_status"],
            "topKw": float(r["top_kw"] or 0),
        } for r in cur.fetchall()}

        cur.execute("""
            SELECT country, count(*) AS n,
                   count(*) FILTER (WHERE vehicle_class = 'HDV') AS hdv
            FROM afir_locations WHERE country IS NOT NULL
            GROUP BY 1 ORDER BY 2 DESC
        """)
        by_country = [{"country": r["country"], "locations": r["n"],
                       "hdv": r["hdv"]} for r in cur.fetchall()]

        cur.execute("SELECT count(*) FROM afir_locations")
        total = cur.fetchone()["count"]

    return {
        "source": "TENtec / AFIR (European Commission)",
        "totalLocations": total,
        "byVehicleClass": by_class,
        "byCountry": by_country,
        "operability": {
            "available": False,
            "note": ("TENtec publishes no plug free/busy status: its recharging "
                     "layer has 24 fields and none of them is availability. "
                     "Live occupancy needs an OCPI/OICP feed per operator."),
        },
    }


def main() -> int:
    settings = Settings()
    OUT.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        hdv = fetch(conn, "HDV")
        ldv = fetch(conn, "LDV")

        (OUT / "afir_hdv.geojson").write_text(
            json.dumps(hdv_geojson(hdv), ensure_ascii=False, separators=(",", ":")))
        (OUT / "afir_ldv.json").write_text(
            json.dumps(ldv_compact(ldv), ensure_ascii=False, separators=(",", ":")))
        (OUT / "afir_summary.json").write_text(
            json.dumps(summary(conn), ensure_ascii=False, indent=2))

    for f in ("afir_hdv.geojson", "afir_ldv.json", "afir_summary.json"):
        p = OUT / f
        print(f"  {f:<24} {p.stat().st_size:>12,} bytes")
    print(f"HDV {len(hdv):,} locations, LDV {len(ldv):,} locations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
