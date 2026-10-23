"""Build the company base layer, province by province."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from depotmon.config import Settings
from depotmon.ingest import osm_companies
from depotmon.store import Store


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provinces", nargs="*", default=list(osm_companies.BOXES))
    args = ap.parse_args()
    settings = Settings()
    with Store(settings.database_url) as store:
        for p in args.provinces:
            try:
                s = osm_companies.run(store, settings, p)
                print(f"{p:16s} elements={s['elements']:6d} named={s['named']:5d} "
                      f"stored={s['stored']:5d}", flush=True)
            except Exception as e:  # keep going: one province failing must not stop the rest
                print(f"{p:16s} ERROR {e}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
