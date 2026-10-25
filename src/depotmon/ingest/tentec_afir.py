"""TENtec / AFIR electric recharging infrastructure for the whole of Europe.

The Commission publishes the Member State AFIR reporting data as an ArcGIS
service, and it is the only Europe-wide source that carries the TEN-T core
network flag, the corridor name, the operator and the per-connector power. That
makes it the natural second layer beside the OSM and Depotcharge data.

Facts about this source that shape the code:

* **CONNECTOR_POWER is in WATTS.** A sample row reads 11000 for an 11 kW Type 2
  connector. Every power test here is in watts and converts to kW once.
* **Rows are per connector, not per site.** One charging pool repeats with
  identical attributes. Rows are aggregated on LOCATION_UNIQUE_ID, so the table
  holds one row per location instead of 1.28 million.
* **About 45% of rows carry no COUNTRY_CODE.** A country filter would silently
  drop them, so the polygon resolver fills the gap from the coordinates.
* **There is no availability data.** The layer has 24 fields and not one is a
  status. `status` is left NULL, and the UI must render NULL as "unknown"
  rather than as "available".
* **Paging is sound.** `resultOffset` + `orderByFields=OBJECTID ASC` returns
  pages with no overlap (verified: two pages of 500, 1000 unique OBJECTIDs).

The service answers only from a host that can reach it: the name resolves to
IPv6 only from some machines, where every request fails with no route.
"""
from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterator

from ..store import Store

ROOT = "https://tentec.transport.ec.europa.eu/api/public/gis"
SERVICE = "Alternative_Fuels"
LAYER_ELECTRIC = 4

PAGE = 2000                 # the service's maxRecordCount
USER_AGENT = "depotchargedex/1.0 psv1900@gmail.com"

# Only the fields the map and the coverage need. `outFields=*` triples the
# transfer for columns nothing reads.
FIELDS = (
    "OBJECTID,LOCATION_UNIQUE_ID,LOCATION_NAME,LOCATION_TYPE,"
    "LOCATION_ADDRESS,LOCATION_CITY,LOCATION_OPERATOR,LOCATION_ACCESSIBILITY,"
    "COUNTRY_CODE,LOCATION_COUNTRY,MEMBER_STATE,CORE_NETWORK,CORRIDORS,"
    "ROUTENAME,CONNECTOR_STANDARD,CONNECTOR_POWERTYPE,CONNECTOR_POWER"
)

# A location is truck-capable at 350 kW (the project's HDV proxy) or with an MCS
# connector, which is the megawatt truck standard and cannot be anything else.
HDV_KW = 350.0

# DBW: thousandths of a watt per kW would be silly; AFIR reports whole watts.
W_PER_KW = 1000.0


def _get(url: str, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            import gzip
            raw = gzip.decompress(raw)
        return json.loads(raw.decode())


def _query(params: dict[str, Any]) -> dict:
    """Query the service, retrying the failures a public ArcGIS endpoint gives.

    `RemoteDisconnected` is the one that bit: the service closes a concurrent
    connection with no response, and that is an OSError, not a URLError, so a
    retry list of (URLError, TimeoutError) let it escape and killed the whole
    run. Catch OSError and HTTPException and back off; a dropped socket is a
    reason to wait, not a reason to lose a continent.
    """
    params = dict(params, f="json")
    url = (f"{ROOT}/{SERVICE}/MapServer/{LAYER_ELECTRIC}/query?"
           + urllib.parse.urlencode(params))
    last: Exception | None = None
    for attempt in range(6):
        try:
            return _get(url)
        except (OSError, http.client.HTTPException, TimeoutError,
                json.JSONDecodeError) as exc:
            last = exc
            if attempt == 5:
                break
            pause = min(30, 2 ** attempt) + (attempt * 0.7)
            print(f"  retry {attempt + 1}/5 in {pause:.1f}s after "
                  f"{type(exc).__name__}: {str(exc)[:100]}", flush=True)
            time.sleep(pause)
    raise RuntimeError(f"query failed after 6 attempts: {type(last).__name__}: "
                       f"{str(last)[:200]}")


def count(where: str = "1=1") -> int:
    d = _query({"where": where, "returnCountOnly": "true"})
    if "error" in d:
        raise RuntimeError(f"count failed: {d['error']}")
    return int(d.get("count", 0))


def iter_connectors(where: str = "1=1") -> Iterator[dict]:
    """Yield raw connector attribute dicts with lon/lat attached."""
    offset = 0
    total = count(where)
    print(f"TENtec AFIR: {total:,} connector rows to fetch "
          f"({(total + PAGE - 1) // PAGE} pages)", flush=True)
    while offset < total:
        d = _query({
            "where": where,
            "outFields": FIELDS,
            "returnGeometry": "true",
            "outSR": "4326",
            "orderByFields": "OBJECTID ASC",
            "resultOffset": offset,
            "resultRecordCount": PAGE,
        })
        if "error" in d:
            raise RuntimeError(f"page at offset {offset} failed: {d['error']}")
        feats = d.get("features", [])
        if not feats:
            # An empty page before `total` means the count and the pages
            # disagree. Stopping silently would publish a partial continent.
            raise RuntimeError(f"empty page at offset {offset} of {total}")
        for f in feats:
            a = dict(f.get("attributes") or {})
            g = f.get("geometry") or {}
            a["_lon"] = g.get("x")
            a["_lat"] = g.get("y")
            yield a
        offset += len(feats)
        if offset % 40000 < PAGE:
            print(f"  {offset:,}/{total:,}", flush=True)


def _access(value: Any) -> str | None:
    v = (value or "").strip().lower()
    if not v:
        return None
    if "semi" in v:
        return "semi-public"
    if "public" in v:
        return "public"
    return v


def bounds(where: str = "1=1") -> tuple[int, int, int]:
    """Return (min OBJECTID, max OBJECTID, row count) for the selection.

    Split by OBJECTID range rather than by resultOffset: the service scans to
    reach an offset (0.5 s at the start, 24 s at 800,000), so offset paging on
    1.28M rows is mostly spent skipping rows it already read.
    """
    stats = json.dumps([
        {"statisticType": "min", "onStatisticField": "OBJECTID",
         "outStatisticFieldName": "mn"},
        {"statisticType": "max", "onStatisticField": "OBJECTID",
         "outStatisticFieldName": "mx"},
        {"statisticType": "count", "onStatisticField": "OBJECTID",
         "outStatisticFieldName": "n"},
    ])
    d = _query({"where": where, "outStatistics": stats,
                "returnGeometry": "false"})
    if "error" in d:
        raise RuntimeError(f"bounds failed: {d['error']}")
    feats = d.get("features") or [{}]
    a = feats[0].get("attributes", {})
    return int(a.get("mn") or 0), int(a.get("mx") or 0), int(a.get("n") or 0)


def _fetch_chunk(where: str, oid_lo: int, oid_hi: int, label: str = "") -> dict:
    """Fetch one OBJECTID range and aggregate it. Runs in a worker thread.

    Keyset paging: each page asks for `OBJECTID > last`, which uses the layer's
    key index and costs about a second at any depth. Measured: 0.75-4.3 s per
    2,000-row page, including a start 800,000 rows deep, against 24 s for the
    equivalent resultOffset page.
    """
    from .country_geom import country_for

    out: dict[str, dict] = {}
    last = oid_lo - 1
    pages = 0
    while True:
        d = _query({
            "where": f"({where}) AND OBJECTID > {last} AND OBJECTID <= {oid_hi}",
            "outFields": FIELDS,
            "returnGeometry": "true",
            "outSR": "4326",
            "orderByFields": "OBJECTID ASC",
            "resultRecordCount": PAGE,
        })
        if "error" in d:
            raise RuntimeError(f"{label} page after {last} failed: {d['error']}")
        feats = d.get("features", [])
        if not feats:
            break
        for f in feats:
            a = dict(f.get("attributes") or {})
            g = f.get("geometry") or {}
            a["_lon"] = g.get("x")
            a["_lat"] = g.get("y")
            _merge_connector(out, a, country_for)
        last = int(feats[-1]["attributes"]["OBJECTID"])
        pages += 1
        if pages % 10 == 0:
            print(f"  {label}: {pages * PAGE:,} rows, at OBJECTID {last:,}",
                  flush=True)
        if len(feats) < PAGE and last >= oid_hi:
            break
    return out


def _merge_connector(out: dict[str, dict], r: dict, country_resolver) -> None:
    """Fold one connector row into its location record."""
    lid = (r.get("LOCATION_UNIQUE_ID") or "").strip()
    if not lid:
        return
    lon, lat = r.get("_lon"), r.get("_lat")
    rec = out.get(lid)
    if rec is None:
        cc = (r.get("COUNTRY_CODE") or "").strip().upper()
        if not cc:
            cc = (r.get("LOCATION_COUNTRY") or "").strip().upper()
        if len(cc) > 2:
            cc = ""
        if not cc and lon is not None and lat is not None:
            # 45% of rows arrive with no country at all. The polygon is the
            # only honest fallback: calling a charger German because the query
            # box was German is the bug that already cost this project 36,881
            # companies.
            cc = (country_resolver(lon, lat) or "") or ""
        rec = out[lid] = {
            "location_id": lid,
            "name": (r.get("LOCATION_NAME") or "").strip() or None,
            "operator": (r.get("LOCATION_OPERATOR") or "").strip() or None,
            "address": (r.get("LOCATION_ADDRESS") or "").strip() or None,
            "city": (r.get("LOCATION_CITY") or "").strip() or None,
            "country": cc or None,
            "lon": lon,
            "lat": lat,
            "max_power_kw": 0.0,
            "n_connectors": 0,
            "standards": set(),
            "access": _access(r.get("LOCATION_ACCESSIBILITY")),
            "core_network": str(r.get("CORE_NETWORK") or "").strip() == "1",
            "corridors": (r.get("CORRIDORS") or "").strip() or None,
            "routename": (r.get("ROUTENAME") or "").strip() or None,
            "location_type": (r.get("LOCATION_TYPE") or "").strip() or None,
        }
    rec["n_connectors"] += 1
    try:
        kw = float(r.get("CONNECTOR_POWER") or 0) / W_PER_KW
    except (TypeError, ValueError):
        kw = 0.0
    if kw > rec["max_power_kw"]:
        rec["max_power_kw"] = kw
    std = (r.get("CONNECTOR_STANDARD") or "").strip()
    if std:
        rec["standards"].add(std)


def aggregate(rows: list[dict], country_resolver) -> list[dict]:
    """Collapse connector rows into one record per charging location."""
    out: dict[str, dict] = {}
    for r in rows:
        _merge_connector(out, r, country_resolver)
    return _finalise(out)


def _finalise(out: dict[str, dict]) -> list[dict]:
    for rec in out.values():
        stds = sorted(rec["standards"])
        rec["standards"] = stds
        hdv = rec["max_power_kw"] >= HDV_KW or "MCS" in stds
        rec["vehicle_class"] = "HDV" if hdv else "LDV"
    return list(out.values())


UPSERT = """
INSERT INTO afir_locations
  (location_id, name, operator, address, city, country, lon, lat, geom,
   max_power_kw, n_connectors, standards, access, vehicle_class,
   core_network, corridors, routename, location_type, source, raw, last_seen)
VALUES
  (%s, %s, %s, %s, %s, %s, %s, %s,
   CASE WHEN %s IS NOT NULL AND %s IS NOT NULL
        THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326) END,
   %s, %s, %s, %s, %s, %s, %s, %s, %s, 'tentec', %s, now())
ON CONFLICT (location_id) DO UPDATE SET
  name = EXCLUDED.name,
  operator = EXCLUDED.operator,
  address = EXCLUDED.address,
  city = EXCLUDED.city,
  country = COALESCE(EXCLUDED.country, afir_locations.country),
  lon = EXCLUDED.lon,
  lat = EXCLUDED.lat,
  geom = COALESCE(EXCLUDED.geom, afir_locations.geom),
  max_power_kw = EXCLUDED.max_power_kw,
  n_connectors = EXCLUDED.n_connectors,
  standards = EXCLUDED.standards,
  access = COALESCE(EXCLUDED.access, afir_locations.access),
  vehicle_class = EXCLUDED.vehicle_class,
  core_network = EXCLUDED.core_network,
  corridors = EXCLUDED.corridors,
  routename = EXCLUDED.routename,
  location_type = EXCLUDED.location_type,
  raw = EXCLUDED.raw,
  last_seen = now()
"""


def _row(rec: dict) -> tuple:
    raw = json.dumps({
        "core_network": rec["core_network"],
        "corridors": rec["corridors"],
        "routename": rec["routename"],
        "location_type": rec["location_type"],
    })
    lon, lat = rec["lon"], rec["lat"]
    return (
        rec["location_id"], rec["name"], rec["operator"], rec["address"],
        rec["city"], rec["country"], lon, lat,
        lon, lat, lon, lat,
        rec["max_power_kw"], rec["n_connectors"], rec["standards"],
        rec["access"], rec["vehicle_class"], rec["core_network"],
        rec["corridors"], rec["routename"], rec["location_type"], raw,
    )


def ingest(store: Store, where: str = "1=1", workers: int = 3,
           batch: int = 25000) -> dict:
    """Pull the layer and upsert one row per charging location.

    Pages are fetched by `workers` threads over disjoint offset ranges, because
    the service serves about 130 rows a second and one stream would take most of
    a day on the full layer. Each worker aggregates its own range, so the merge
    is a dict update and nothing is lost between them: a location's connectors
    are contiguous by OBJECTID in practice, and even if they were not, the
    merge takes the maximum power and the union of the standards.
    """
    run_id = store.start_run("ingest", "tentec_afir")
    kept = 0
    total = 0
    try:
        oid_lo, oid_hi, total = bounds(where)
        print(f"TENtec AFIR: {total:,} connectors, OBJECTID {oid_lo:,}-{oid_hi:,}, "
              f"{workers} workers", flush=True)
        if total == 0:
            store.finish_run(run_id, status="ok", records_in=0, records_out=0)
            return {"connectors": 0, "locations": 0}

        span = (oid_hi - oid_lo + 1 + workers - 1) // workers
        ranges = [(oid_lo + i * span, min(oid_hi, oid_lo + (i + 1) * span - 1))
                  for i in range(workers)]
        merged: dict[str, dict] = {}
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_chunk, where, a, b, f"w{i}"): (a, b)
                for i, (a, b) in enumerate(ranges) if a <= b
            }
            for fut in as_completed(futures):
                a, b = futures[fut]
                chunk = fut.result()
                merged.update(chunk)
                done += 1
                print(f"  worker range {a:,}-{b:,} done: {len(chunk):,} locations "
                      f"({done}/{len(futures)} workers, {len(merged):,} so far)",
                      flush=True)

        recs = _finalise(merged)
        print(f"merged to {len(recs):,} locations; writing", flush=True)
        with store.tx() as cur:
            for i in range(0, len(recs), batch):
                part = recs[i:i + batch]
                cur.executemany(UPSERT, [_row(x) for x in part])
                kept += len(part)
                print(f"  wrote {kept:,}/{len(recs):,}", flush=True)

        store.finish_run(run_id, status="ok", records_in=total, records_out=kept)
        store.record_health("tentec_afir", True, kept)
        print(f"TENtec AFIR: {total:,} connectors -> {kept:,} locations",
              flush=True)
        return {"connectors": total, "locations": kept}
    except Exception as exc:
        store.finish_run(run_id, status="failed", error=str(exc)[:2000])
        store.record_health("tentec_afir", False, kept, error=str(exc)[:500])
        store.alert("source_failure", f"tentec_afir failed: {str(exc)[:300]}",
                    source="tentec_afir")
        raise
