#!/usr/bin/env python3
"""Repair the country on the Europe-wide OSM rows that were ingested wrong.

The ingest labelled every site with the country of the Overpass QUERY BOX
instead of the country of its own coordinates, so 256 of 1,800 rows carry a
neighbouring country's code.

This recomputes the country for every `osm_eu` row from the real polygons and
writes it back. It is a repair, not a normal step: run it once after the fix
in `src/depotmon/ingest/osm_eu.py` has been deployed, then it has nothing
left to do.

The signal's `content_hash` includes the tags but not the country, so the
hash does not change and the row is not re-ingested on the next run. The
country lives in `raw_state`, and the `signals.country` column is GENERATED
from `raw_state`, so one UPDATE fixes both.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from depotmon.config import Settings
from depotmon.ingest.country_geom import country_for
from depotmon.ingest.osm_eu import ISO2_TO_ISO3
from depotmon.store import Store


def main() -> int:
    settings = Settings()
    fixed = 0
    unchanged = 0
    blanked = 0
    with Store(settings.database_url) as store:
        with store.conn.cursor() as cur:
            cur.execute(
                """
                SELECT signal_id, lon, lat, raw_state->>'country' AS old_country
                  FROM signals
                 WHERE source = 'osm_eu' AND lon IS NOT NULL AND lat IS NOT NULL
                """
            )
            rows = cur.fetchall()
        for row in rows:
            actual = country_for(float(row["lon"]), float(row["lat"]))
            old = row["old_country"] or ""
            if not actual:
                if old:
                    # No polygon contains this point. Drop the claim rather
                    # than keep a label we cannot stand behind.
                    blanked += 1
                    with store.conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE signals
                               SET raw_state = jsonb_set(
                                       jsonb_set(raw_state, '{country}', 'null'::jsonb, true),
                                       '{countryIso3}', 'null'::jsonb, true)
                             WHERE signal_id = %s
                            """,
                            (row["signal_id"],),
                        )
                continue
            if actual == old:
                unchanged += 1
                continue
            with store.conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE signals
                       SET raw_state = jsonb_set(
                               jsonb_set(raw_state, '{country}', to_jsonb(%s::text), true),
                               '{countryIso3}', to_jsonb(%s::text), true)
                     WHERE signal_id = %s
                    """,
                    (actual, ISO2_TO_ISO3.get(actual, ""), row["signal_id"]),
                )
            fixed += 1
    print(json.dumps({
        "checked": len(rows),
        "fixed": fixed,
        "unchanged": unchanged,
        "blanked": blanked,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
