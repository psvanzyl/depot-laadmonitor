#!/usr/bin/env python3
"""Time each stage of the AFIR ingest on a small subset.

The 350 kW subset is 32,222 connectors over 17 pages, and pages cost 4 s, so
the fetch is under a minute. The run still blew past seven minutes, so the cost
is either the country polygon resolver or the upsert. This measures both
directly instead of guessing.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, "/root/projects/depot-laadmonitor/src")

from depotmon.ingest import tentec_afir  # noqa: E402
from depotmon.ingest.country_geom import country_for  # noqa: E402

WHERE = "CONNECTOR_POWER >= 350000"

t0 = time.time()
n = tentec_afir.count(WHERE)
print(f"count(): {n:,} rows in {time.time() - t0:.1f}s", flush=True)

t0 = time.time()
rows = []
for i, r in enumerate(tentec_afir.iter_connectors(WHERE)):
    rows.append(r)
    if i >= 5999:
        break
print(f"fetched {len(rows):,} connector rows in {time.time() - t0:.1f}s", flush=True)

t0 = time.time()
recs = tentec_afir.aggregate(rows, country_for)
print(f"aggregate(): {len(recs):,} locations in {time.time() - t0:.1f}s", flush=True)

missing = [r for r in recs if not r["country"]]
print(f"  locations without a country after resolution: {len(missing):,}", flush=True)

# Time the resolver alone on the points that had no country code, so the
# polygon cost is visible rather than folded into aggregate().
t0 = time.time()
for r in missing[:500]:
    country_for(r["lon"], r["lat"])
print(f"country_for() on 500 points: {time.time() - t0:.2f}s "
      f"({(time.time() - t0) / 500 * 1000:.1f} ms each)", flush=True)

hdv = sum(1 for r in recs if r["vehicle_class"] == "HDV")
print(f"  HDV {hdv:,} / LDV {len(recs) - hdv:,}", flush=True)
