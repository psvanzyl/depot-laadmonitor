#!/usr/bin/env python3
"""Repair the company country labels from the polygons.

The Europe company ingest labelled every company with the country of the
Overpass query box it was found in. The German box contains parts of the
Netherlands, Belgium, Austria, Switzerland, Luxembourg and Czechia, so those
companies were labelled German. Re-running the ingest would take hours and
would just re-derive the same wrong labels unless the ingest is fixed; it is
(osm_companies_eu.company_country). This repairs the rows already in the
database, without a re-ingest.

For every company with a point, the polygon resolver decides the country. A
point the polygon cannot resolve keeps its current label, because a missing
polygon is not evidence that the current label is wrong.

The output lists, per country, how many rows changed, so the correction is
visible rather than trusted.

Usage:
    .venv/bin/python scripts/repair_company_country.py            # report only
    .venv/bin/python scripts/repair_company_country.py --apply    # write
"""
from __future__ import annotations

import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from depotmon.config import Settings  # noqa: E402
from depotmon.ingest.osm_eu import country_of  # noqa: E402
from depotmon.store import Store  # noqa: E402

BATCH = 2000


def main() -> int:
    apply = "--apply" in sys.argv
    settings = Settings()
    with Store(settings.database_url) as store:
        cur = store.conn.cursor()
        cur.execute(
            """
            SELECT company_id,
                   COALESCE(NULLIF(country, ''), '') AS current,
                   ST_X(geom) AS lon, ST_Y(geom) AS lat
              FROM companies
             WHERE geom IS NOT NULL
            """
        )
        rows = cur.fetchall()
        print(f"companies with a point: {len(rows)}")

        moves: Counter = Counter()
        unresolved = 0
        changed: list[tuple[str, int]] = []
        for r in rows:
            found = country_of(float(r["lon"]), float(r["lat"]))
            if not found:
                unresolved += 1
                continue
            if found != r["current"]:
                moves[(r["current"] or "?", found)] += 1
                changed.append((found, r["company_id"]))

        print(f"unresolved by the polygon (label kept): {unresolved}")
        print(f"rows to relabel: {len(changed)}")
        print("\nbiggest moves (from -> to):")
        for (src, dst), n in moves.most_common(20):
            print(f"  {src:>3} -> {dst:<3} {n:>7,}")

        if not apply:
            print("\nreport only. re-run with --apply to write.")
            return 0

        with store.tx() as tx:
            for i in range(0, len(changed), BATCH):
                chunk = changed[i:i + BATCH]
                tx.executemany(
                    "UPDATE companies SET country = %s WHERE company_id = %s",
                    chunk,
                )

        cur.execute(
            "SELECT country, count(*) AS n FROM companies "
            "WHERE country IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
        )
        print("\nafter repair:")
        for r in cur.fetchall():
            print(f"  {r['country']:>3} {r['n']:>7,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
