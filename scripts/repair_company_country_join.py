#!/usr/bin/env python3
"""Repair the company country labels with one indexed join.

Why this shape
--------------
The previous attempt used a correlated subquery per row:

    (SELECT p.code FROM _country_poly p
      WHERE ST_Contains(p.geom, c.geom)
      ORDER BY ST_Area(p.geom) LIMIT 1)

The ORDER BY forces a sort of every containing polygon for every one of
217,484 companies, and the subquery cannot become a join. It did not finish
in ten minutes.

This does the same work as one join, with no per-row sort:

    SELECT c.company_id, p.code
      FROM companies c JOIN _country_poly p ON ST_Contains(p.geom, c.geom)

`DISTINCT ON (company_id)` then picks one code per company. Where two polygons
overlap on a border sliver both codes are returned; the smaller country by
area wins, which is the rule the Python resolver used. That tie-break is
resolved AFTER the join, over the handful of rows that need it, not per row.

Usage:
    .venv/bin/python scripts/repair_company_country_join.py [--apply]
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
    gj = json.loads(GEOJSON.read_text())

    with Store(Settings().database_url) as store:
        cur = store.conn.cursor()
        with store.tx() as tx:
            tx.execute("DROP TABLE IF EXISTS _cpoly")
            tx.execute(
                "CREATE TABLE _cpoly (code text, geom geometry(MultiPolygon,4326))"
            )
            n = 0
            for f in gj["features"]:
                props = f["properties"]
                code = (
                    props.get("ISO_A2")
                    or props.get("iso_a2")
                    or props.get("ISO3166-1-Alpha-2")
                    or props.get("id")
                    or ""
                )
                if not code or str(code) in {"-99", "NA"} or len(str(code)) != 2:
                    continue
                geom = f["geometry"]
                if geom["type"] == "Polygon":
                    geom = {"type": "MultiPolygon", "coordinates": [geom["coordinates"]]}
                tx.execute(
                    "INSERT INTO _cpoly VALUES (%s, ST_Multi(ST_GeomFromGeoJSON(%s)))",
                    (str(code).upper(), json.dumps(geom)),
                )
                n += 1
            tx.execute("CREATE INDEX ON _cpoly USING GIST (geom)")
            tx.execute("ANALYZE _cpoly")
        print(f"polygons: {n}", flush=True)

        # One join, no per-row sort.
        with store.tx() as tx:
            tx.execute("DROP TABLE IF EXISTS _cfix")
            tx.execute(
                """
                CREATE UNLOGGED TABLE _cfix AS
                SELECT DISTINCT ON (c.company_id)
                       c.company_id,
                       COALESCE(NULLIF(c.country,''),'') AS current,
                       p.code AS found
                  FROM companies c
                  JOIN _cpoly p ON ST_Contains(p.geom, c.geom)
                 WHERE c.geom IS NOT NULL
                 ORDER BY c.company_id, ST_Area(p.geom)
                """
            )
            tx.execute("CREATE INDEX ON _cfix (company_id)")
        print("join done", flush=True)

        cur.execute("SELECT count(*) AS n FROM _cfix")
        print(f"resolved by a polygon: {cur.fetchone()['n']:,}", flush=True)
        cur.execute(
            "SELECT count(*) AS n FROM companies "
            "WHERE geom IS NOT NULL AND company_id NOT IN (SELECT company_id FROM _cfix)"
        )
        print(f"not resolved (label kept): {cur.fetchone()['n']:,}", flush=True)

        cur.execute(
            """
            SELECT current, found, count(*) AS n FROM _cfix
             WHERE found <> current GROUP BY 1,2 ORDER BY n DESC
            """
        )
        moves = cur.fetchall()
        print(f"rows to relabel: {sum(r['n'] for r in moves):,}", flush=True)
        for r in moves[:15]:
            print(f"  {r['current'] or '?':>3} -> {r['found']:<3} {r['n']:>7,}", flush=True)

        if not apply:
            with store.tx() as tx:
                tx.execute("DROP TABLE IF EXISTS _cfix")
                tx.execute("DROP TABLE IF EXISTS _cpoly")
            print("report only. re-run with --apply.", flush=True)
            return 0

        with store.tx() as tx:
            tx.execute(
                """
                UPDATE companies c SET country = f.found
                  FROM _cfix f
                 WHERE c.company_id = f.company_id
                   AND f.found <> f.current
                """
            )
            updated = tx.rowcount
        print(f"updated: {updated:,}", flush=True)

        cur.execute(
            "SELECT country, count(*) AS n FROM companies WHERE country IS NOT NULL "
            "GROUP BY 1 ORDER BY 2 DESC"
        )
        print("after repair:", flush=True)
        for r in cur.fetchall():
            print(f"  {r['country']:>3} {r['n']:>7,}", flush=True)

        with store.tx() as tx:
            tx.execute("DROP TABLE IF EXISTS _cfix")
            tx.execute("DROP TABLE IF EXISTS _cpoly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
