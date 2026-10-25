#!/usr/bin/env python3
"""Ingest the TENtec / AFIR recharging infrastructure layer.

    scripts/ingest_tentec.py                 # everything (~1.28M connectors)
    scripts/ingest_tentec.py --hdv-only      # 150 kW and above
    scripts/ingest_tentec.py --count         # sizes only, no writes

The layer is a public ArcGIS service with no auth, but only a host that can
reach it may run this: from some machines the name resolves to IPv6 only and
every request fails with no route, so run it on CT141.

Light duty is loaded by default, not skipped, because the site has to be able
to show it and then filter it out. `--hdv-only` exists for a quick refresh that
does not spend half an hour on passenger-car chargers.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from depotmon.config import Settings
from depotmon.ingest import tentec_afir
from depotmon.store import Store

# 150 kW is the AFIR threshold for a heavy-duty-capable pool, and it keeps the
# quick refresh to a sixth of the row count.
HDV_WHERE = "CONNECTOR_POWER >= 150000"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hdv-only", action="store_true",
                    help="only connectors at 150 kW and above")
    ap.add_argument("--count", action="store_true",
                    help="print sizes and exit")
    ap.add_argument("--where", default=None, help="override the WHERE clause")
    args = ap.parse_args()

    where = args.where or (HDV_WHERE if args.hdv_only else "1=1")

    if args.count:
        print(json.dumps({
            "where": where,
            "all_connectors": tentec_afir.count("1=1"),
            "selected_connectors": tentec_afir.count(where),
        }, indent=2))
        return 0

    settings = Settings()
    with Store(settings.database_url) as store:
        print(json.dumps(tentec_afir.ingest(store, where=where), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
