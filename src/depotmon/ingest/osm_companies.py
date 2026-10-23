"""Ingest transport/logistics company premises in NL from OpenStreetMap.

Why this exists: a depot charger is only meaningful at a company's own yard. Before
this, a news article about "Oegema in Apeldoorn" was geocoded to the city centroid,
which puts a truck depot in a shopping street. So we first build a base layer of
*companies at their premises* (name + address + point), then attach depot evidence to
the matching company instead of to a city.

OSM is the free, licence-clean base. KvK (ingest/kvk.py) adds the official register
when an API key is configured; both land in the same `companies` table.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from ..fetch import Fetcher
from ..news_extract import normalize_company
from ..store import Store

OVERPASS = "https://overpass-api.de/api/interpreter"

# Rough province boxes keep each Overpass response small enough to finish.
BOXES: dict[str, tuple[float, float, float, float]] = {
    "groningen": (6.10, 52.90, 7.30, 53.60),
    "friesland": (5.10, 52.80, 6.40, 53.50),
    "drenthe": (6.10, 52.60, 7.10, 53.10),
    "overijssel": (5.70, 52.20, 7.10, 52.80),
    "flevoland": (5.20, 52.30, 6.00, 52.80),
    "gelderland": (5.00, 51.70, 6.90, 52.50),
    "utrecht": (4.80, 51.90, 5.70, 52.30),
    "noord-holland": (4.50, 52.20, 5.30, 53.00),
    "zuid-holland": (3.90, 51.70, 4.90, 52.20),
    "zeeland": (3.30, 51.20, 4.30, 51.80),
    "noord-brabant": (4.20, 51.20, 6.10, 51.90),
    "limburg": (5.50, 50.70, 6.30, 51.80),
}

SELECTORS = """
  nwr["office"~"^(logistics|transport|moving_company|freight_forwarding|courier)$"]({b});
  nwr["industrial"="logistics"]({b});
  nwr["building"="warehouse"]["name"]({b});
  nwr["landuse"="industrial"]["operator"]({b});
  nwr["landuse"="industrial"]["name"]({b});
  nwr["building"="industrial"]["name"]({b});
"""

CARRIER_HINT = (
    "transport", "vervoer", "trucking", "logistics", "logistiek", "cargo", "fracht",
    "spedition", "distributie", "koerier", "courier", "verhuizing", "moving",
)


def build_query(box: tuple[float, float, float, float]) -> str:
    w, s, e, n = box
    b = f"{s},{w},{n},{e}"
    return f"[out:json][timeout:180];\n(\n{SELECTORS.format(b=b)}\n);\nout center tags;"


def _kind(tags: dict) -> str:
    blob = " ".join(str(v) for v in tags.values() if isinstance(v, str)).lower()
    if any(h in blob for h in CARRIER_HINT):
        return "carrier"
    if tags.get("building") == "warehouse" or tags.get("landuse") == "industrial":
        return "shipper"
    return "unknown"


def _fetch_box(settings, box) -> dict:
    """Query one box, retrying once; caller splits the box if this keeps failing."""
    import time

    url = OVERPASS + "?" + urlencode({"data": build_query(box)})
    last = None
    for attempt in range(2):
        with Fetcher(settings, robots_exempt={"overpass-api.de": "Overpass API is a query API, robots.txt does not apply to ?data= queries"}) as fetcher:
            resp = fetcher.get(url)
        if resp.status == 200:
            return json.loads(resp.content)
        last = resp.status
        time.sleep(12 * (attempt + 1))
    raise RuntimeError(f"overpass HTTP {last}")


def quadrants(box):
    w, s_, e, n = box
    mx, my = (w + e) / 2, (s_ + n) / 2
    return [(w, s_, mx, my), (mx, s_, e, my), (w, my, mx, n), (mx, my, e, n)]


def run(store: Store, settings, province: str) -> dict:
    box = BOXES[province]
    try:
        payload = _fetch_box(settings, box)
    except RuntimeError:
        # too big for one query: four smaller boxes always succeed
        merged = {"elements": []}
        for q in quadrants(box):
            merged["elements"].extend(_fetch_box(settings, q).get("elements", []))
        payload = merged
    stats = {"province": province, "elements": 0, "named": 0, "stored": 0, "skipped": 0}
    rows = []
    for el in payload.get("elements", []):
        stats["elements"] += 1
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
        stats["named"] += 1
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
                """INSERT INTO companies(name, name_norm, kind, address, postcode, city,
                        geom, source, source_url, external_id, tags)
                   VALUES (%(name)s, %(name_norm)s, %(kind)s, %(address)s, %(postcode)s,
                        %(city)s, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326),
                        %(source)s, %(source_url)s, %(external_id)s, %(tags)s::jsonb)
                   ON CONFLICT (source, external_id) DO UPDATE
                      SET name = EXCLUDED.name, name_norm = EXCLUDED.name_norm,
                          address = COALESCE(EXCLUDED.address, companies.address),
                          postcode = COALESCE(EXCLUDED.postcode, companies.postcode),
                          city = COALESCE(EXCLUDED.city, companies.city),
                          geom = EXCLUDED.geom, last_seen = now()""",
                r,
            )
            stats["stored"] += 1
    return stats
