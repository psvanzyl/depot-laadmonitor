"""Company base layer for the whole of Europe (OSM logistics premises).

The Dutch layer (`osm_companies.py`) walks 12 province boxes. That does not
scale to Europe, and the coverage analysis needs a company base layer per
country: a charger in Germany only "covers" a company if that company is
within 500 m of it, so the company layer must exist where the chargers are.

Same approach as `osm_eu.py`: one Overpass query per country, one `runs` row
per country, so a partial failure is visible instead of hidden.

Countries are processed in descending order of charging density, so a partial
run still leaves the most valuable coverage in place.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace

from ..config import Settings
from ..fetch import Fetcher
from ..news_extract import normalize_company
from ..store import Store

from .osm_eu import (EUROPE_BOXES, ISO2_TO_ISO3, MIRRORS,
                       OVERPASS_EXEMPT, PRIORITY_ORDER)

OVERPASS_TIMEOUT = 300.0

# The same selector set as the Dutch layer, widened with the German and
# French tags that carry most of the European logistics POIs.
SELECTORS = """
  nwr["office"~"^(logistics|transport|moving_company|freight_forwarding|courier|freight)$"]({b});
  nwr["industrial"="logistics"]({b});
  nwr["building"="warehouse"]["name"]({b});
  nwr["landuse"="industrial"]["operator"]({b});
  nwr["landuse"="industrial"]["name"]({b});
  nwr["building"="industrial"]["name"]({b});
  nwr["shop"="truck"]({b});
  nwr["amenity"="truck_stop"]({b});
"""

CARRIER_HINT = (
    "transport", "vervoer", "trucking", "logistics", "logistiek", "cargo", "fracht",
    "spedition", "distributie", "koerier", "courier", "verhuizing", "moving",
    "spedition", "transport", "fracht", "logistik", "cargo", "shipping",
    "haulage", "ttransport", "entrepot", "dépôt", "depot",
)


def _kind(tags: dict) -> str:
    blob = " ".join(str(v) for v in tags.values() if isinstance(v, str)).lower()
    if any(h in blob for h in CARRIER_HINT):
        return "carrier"
    if tags.get("building") == "warehouse" or tags.get("landuse") == "industrial":
        return "shipper"
    return "unknown"


def build_query(box: tuple[float, float, float, float]) -> str:
    s, w, n, e = box
    b = f"{s},{w},{n},{e}"
    return f"[out:json][timeout:280];\n(\n{SELECTORS.format(b=b)}\n);\nout center tags;"


def quadrants(box: tuple[float, float, float, float]):
    s, w, n, e = box
    mx, my = (w + e) / 2, (s + n) / 2
    return [(s, w, my, mx), (s, mx, my, e), (my, w, n, mx), (my, mx, n, e)]


def _fetch(settings, store, run_id, box) -> dict:
    query = build_query(box)
    osm_settings = replace(settings, http_timeout=OVERPASS_TIMEOUT, max_retries=1)
    attempts = []
    for mirror in MIRRORS:
        host = mirror.split("/")[2]
        started = time.monotonic()
        try:
            with Fetcher(osm_settings, store=store, run_id=run_id, source="osm_co_eu",
                         robots_exempt=OVERPASS_EXEMPT) as fetcher:
                resp = fetcher.get(mirror, method="POST", data={"data": query})
            elapsed = round(time.monotonic() - started, 1)
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            payload = json.loads(resp.content)
            elements = payload.get("elements") or []
            if not elements:
                raise RuntimeError("empty result (HTTP 200 but 0 elements)")
            attempts.append({"mirror": host, "ok": True, "s": elapsed, "n": len(elements)})
            return {"elements": elements, "attempts": attempts}
        except Exception as exc:
            attempts.append({"mirror": host, "ok": False,
                             "s": round(time.monotonic() - started, 1),
                             "err": str(exc)[:120]})
    raise RuntimeError(f"all mirrors failed: {attempts[-1] if attempts else '?'}")


def run(store: Store, settings: Settings, countries: list[str] | None = None) -> dict:
    targets = countries or PRIORITY_ORDER
    overall: dict = {"countries": {}, "failed": [], "stored": 0}
    for code in targets:
        box = EUROPE_BOXES.get(code)
        if not box:
            continue
        run_id = store.start_run("ingest", f"osm_co_eu:{code}")
        try:
            try:
                payload = _fetch(settings, store, run_id, box)
            except RuntimeError:
                merged = {"elements": []}
                for q in quadrants(box):
                    merged["elements"].extend(_fetch(settings, store, run_id, q)["elements"])
                payload = merged
            stats = {"elements": len(payload["elements"]), "stored": 0, "skipped": 0}
            rows = []
            for el in payload["elements"]:
                tags = el.get("tags") or {}
                name = (tags.get("name") or tags.get("operator") or "").strip()
                if not name or len(name) < 3:
                    stats["skipped"] += 1
                    continue
                center = el.get("center") or {}
                lon = el.get("lon", center.get("lon"))
                lat = el.get("lat", center.get("lat"))
                if lon is None or lat is None:
                    stats["skipped"] += 1
                    continue
                addr = " ".join(
                    p for p in (tags.get("addr:street"), tags.get("addr:housenumber")) if p
                ).strip() or None
                rows.append(
                    {
                        "name": name,
                        "name_norm": normalize_company(name),
                        "kind": _kind(tags),
                        "address": addr,
                        "postcode": (tags.get("addr:postcode") or "").strip() or None,
                        "city": (tags.get("addr:city") or "").strip() or None,
                        "lon": float(lon),
                        "lat": float(lat),
                        "source": "osm",
                        "source_url": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
                        "external_id": f"{el['type']}/{el['id']}",
                        "tags": json.dumps(tags)[:4000],
                    }
                )
            with store.tx() as cur:
                for r in rows:
                    cur.execute(
                        """
                        INSERT INTO companies(name, name_norm, kind, address, postcode, city,
                                geom, source, source_url, external_id, tags, country)
                           VALUES (%(name)s, %(name_norm)s, %(kind)s, %(address)s, %(postcode)s,
                                %(city)s, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326),
                                %(source)s, %(source_url)s, %(external_id)s, %(tags)s::jsonb,
                                %(country)s)
                           ON CONFLICT (source, external_id) DO UPDATE
                              SET name = EXCLUDED.name, name_norm = EXCLUDED.name_norm,
                                  address = COALESCE(EXCLUDED.address, companies.address),
                                  postcode = COALESCE(EXCLUDED.postcode, companies.postcode),
                                  city = COALESCE(EXCLUDED.city, companies.city),
                                  country = EXCLUDED.country,
                                  geom = EXCLUDED.geom, last_seen = now()
                        """,
                        {**r, "country": code},
                    )
                    stats["stored"] += 1
            overall["countries"][code] = stats
            overall["stored"] += stats["stored"]
            store.record_health(f"osm_co_eu:{code}", True, stats["stored"])
            store.finish_run(run_id, status="ok", records_in=stats["elements"],
                             records_out=stats["stored"], note=json.dumps(stats))
            print(f"[osm_co_eu] {code}: {stats['stored']} companies")
        except Exception as exc:
            overall["failed"].append(code)
            store.record_health(f"osm_co_eu:{code}", False, 0, error=str(exc)[:500])
            store.alert("source_failure", f"osm_co_eu {code} failed: {exc}",
                        source=f"osm_co_eu:{code}")
            store.finish_run(run_id, status="failed", error=str(exc)[:2000])
            print(f"[osm_co_eu] {code}: FAILED {exc}")
    return overall
