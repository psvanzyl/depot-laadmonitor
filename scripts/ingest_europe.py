#!/usr/bin/env python3
"""Run the Europe-wide OSM ingests, one country at a time.

The two ingests are separate because they answer different questions:

    osm_eu        truck-capable charge points (the product's own subject)
    osm_companies_eu  logistics premises (the base layer that makes the
                      coverage analysis meaningful)

Run one, the other, or both:

    scripts/ingest_europe.py --chargers
    scripts/ingest_europe.py --companies
    scripts/ingest_europe.py --chargers --companies --countries DE,FR,NL

Every country is its own `runs` row, so a country that fails the Overpass
rotation is visible in source_health and can be retried on its own later.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from depotmon.config import Settings
from depotmon.ingest import osm_companies_eu, osm_eu
from depotmon.store import Store


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chargers", action="store_true", help="run the charge-point ingest")
    ap.add_argument("--companies", action="store_true", help="run the company ingest")
    ap.add_argument("--countries", nargs="*", default=None,
                    help="ISO-2 country codes; default is every European country")
    args = ap.parse_args()

    if not (args.chargers or args.companies):
        ap.error("choose at least one of --chargers / --companies")

    settings = Settings()
    countries = [c.strip().upper() for c in (args.countries or [])] or None
    results = {}
    with Store(settings.database_url) as store:
        if args.chargers:
            results["chargers"] = osm_eu.run(store, settings, countries)
        if args.companies:
            results["companies"] = osm_companies_eu.run(store, settings, countries)
    print(json.dumps(
        {k: {"failed": v.get("failed"), "stored": v.get("stored"), "kept": v.get("kept")}
         for k, v in results.items()},
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
