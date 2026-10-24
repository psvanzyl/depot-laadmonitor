#!/usr/bin/env python3
"""Repair the company country labels with PostGIS, not Python.

The Python point-in-polygon loop did not finish 217,484 companies inside ten
minutes: every point walks the vertices of every country whose bounding box it
falls inside, and that is hundreds of millions of vertex tests in the
interpreter.

PostGIS does the same test in C with a GiST index. The country polygons are
loaded once into a table and `ST_Contains` decides the country; where two
polygons overlap (a border sliver), the smaller area wins, which is the same
rule the Python resolver used.

The output lists the moves, so the correction is visible.

Usage:
    .venv/bin/python scripts/repair_company_country_pg.py            # report
    .venv/bin/python scripts/repair_company_country_pg.py --apply    # write
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from depotmon.config import Settings  # noqa: E402
from depotmon.store import Store  # noqa: E402

GEOJSON = pathlib.Path(__file__).resolve().parents[1] / "resources/geo/countries-europe.geojson"


def main() -> int:
    apply = "--apply" in sys.argv
    settings = Settings()
    gj = json.loads(GEOJSON.read_text())
    feats = gj["features"]
    print(f"country polygons: {len(feats)}")

    with Store(settings.database_url) as store:
        cur = store.conn.cursor()

        # A scratch table, so the polygons are in the database and the index
        # does the work. Dropped at the end.
        with store.tx() as tx:
            tx.execute("DROP TABLE IF EXISTS _country_poly")
            tx.execute(
                """
                CREATE TABLE _country_poly (
                    code text PRIMARY KEY,
                    geom geometry(MultiPolygon, 4326) NOT NULL
                )
                """
            )
            for f in feats:
                props = f["properties"]
                code = (
                    props.get("ISO_A2")
                    or props.get("iso_a2")
                    or props.get("ISO3166-1-Alpha-2")
                    or props.get("id")
                    or ""
                )
                if not code or code in {"-99", "NA"} or len(str(code)) != 2:
                    continue
                geom = f["geometry"]
                if geom["type"] == "Polygon":
                    geom = {"type": "MultiPolygon", "coordinates": [geom["coordinates"]]}
                tx.execute(
                    "INSERT INTO _country_poly(code, geom) "
                    "VALUES (%s, ST_Multi(ST_GeomFromGeoJSON(%s))) "
                    "ON CONFLICT (code) DO NOTHING",
                    (str(code).upper(), json.dumps(geom)),
                )
            tx.execute("CREATE INDEX ON _country_poly USING GIST (geom)")

        cur.execute("SELECT count(*) AS n FROM _country_poly")
        print(f"polygons loaded: {cur.fetchone()['n']}")

        # How many rows would move.
        cur.execute(
            """
            WITH resolved AS (
                SELECT c.company_id,
                       COALESCE(NULLIF(c.country, ''), '') AS current,
                       (SELECT p.code FROM _country_poly p
                         WHERE ST_Contains(p.geom, c.geom)
                         ORDER BY ST_Area(p.geom) LIMIT 1) AS found
                  FROM companies c
                 WHERE c.geom IS NOT NULL
            )
            SELECT current, found, count(*) AS n
              FROM resolved
             WHERE found IS NOT NULL AND found <> current
             GROUP BY 1, 2
             ORDER BY n DESC
            """
        )
        moves = cur.fetchall()
        total_moves = sum(r["n"] for r in moves)
        print(f"rows to relabel: {total_moves}")
        for r in moves[:20]:
            print(f"  {r['current'] or '?':>3} -> {r['found']:<3} {r['n']:>7,}")

        if not apply:
            with store.tx() as tx:
                tx.execute("DROP TABLE IF EXISTS _country_poly")
            print("\nreport only. re-run with --apply to write.")
            return 0

        with store.tx() as tx:
            # Materialise the resolution once. Doing ST_Contains inside the
            # UPDATE recomputes the spatial lookup for every row and did not
            # finish in eight minutes; one scan into a table then an UPDATE
            # from it does.
            tx.execute("DROP TABLE IF EXISTS _company_fix")
            tx.execute(
                """
                CREATE UNLOGGED TABLE _company_fix AS
                SELECT c.company_id,
                       COALESCE(NULLIF(c.country, ''), '') AS current,
                       (SELECT p.code FROM _country_poly p
                         WHERE ST_Contains(p.geom, c.geom)
                         ORDER BY ST_Area(p.geom) LIMIT 1) AS found
                  FROM companies c
                 WHERE c.geom IS NOT NULL
                """
            )
            tx.execute(
                "CREATE INDEX ON _company_fix (company_id)"
            )

        cur.execute(
            "SELECT count(*) AS n FROM _company_fix "
            "WHERE found IS NOT NULL AND found <> current"
        )
        print(f"rows to relabel: {cur.fetchone()['n']:,}")

        with store.tx() as tx:
            tx.execute(
                """
                UPDATE companies c
                   SET country = f.found
                  FROM _company_fix f
                 WHERE c.company_id = f.company_id
                   AND f.found IS NOT NULL
                   AND f.found <> f.current
                """
            )
            updated = tx.rowcount

        cur.execute(
            "SELECT country, count(*) AS n FROM companies "
            "WHERE country IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
        )
        print(f"\nrows updated: {updated}")
        print("after repair:")
        for r in cur.fetchall():
            print(f"  {r['country']:>3} {r['n']:>7,}")

        with store.tx() as tx:
            tx.execute("DROP TABLE IF EXISTS _company_fix")
            tx.execute("DROP TABLE IF EXISTS _country_poly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
