"""Ingest: OpenStreetMap Overpass — truck-relevant and non-public charge points.

Only the subset that OSM is uniquely good at is queried: hgv tagging, MCS sockets and
private/customer-only access. Public forecourts are already covered (better) by NDW.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import replace

from ..config import Settings
from ..fetch import Fetcher
from ..store import Store, as_json

# A national Overpass query legitimately takes 60-180s. The global 90s socket
# timeout with 3 retries x 3 mirrors meant up to nine doomed attempts, which is
# how a "source failure" turned into a five minute stall and a failed unit.
OVERPASS_TIMEOUT = 240.0

# Ordered by measured reliability from this host. Timings are a national bbox
# query, 2026-10-23: overpass-api.de ~67s (3357 elements), private.coffee ~159s
# (3141), kumi.systems timed out at 240s, osm.ch returned an empty 200 body in
# 0.2s. Dead mirrors stay last so they are only reached if everything else fails.
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# bbox, not area["ISO3166-1"="NL"]: an area lookup costs the server far more than
# a bbox and was the main cause of timeouts. Order is south,west,north,east and
# covers the Netherlands including the Wadden islands.
QUERY = """
[out:json][timeout:180][bbox:50.6,3.2,53.6,7.3];
(
  nwr["amenity"="charging_station"]["hgv"];
  nwr["amenity"="charging_station"]["socket:mcs"];
  nwr["amenity"="charging_station"]["access"~"^(private|customers|no|permit)$"];
);
out center tags;
""".strip()

# overpass-api.de/robots.txt carries "Disallow: /api/" for "User-agent: *", so
# the polite fetcher refuses its /api/interpreter endpoint. That endpoint is a
# documented query API for programs, not crawlable content, and this project
# issues one national query per day with a contact address in the UA. The reason
# is logged and stored with every run.
OVERPASS_EXEMPT = {
    "overpass-api.de": (
        "/api/interpreter is a documented query API, not crawlable content; "
        "robots.txt Disallow: /api/ targets crawlers. Policy honoured by sending "
        "one national query per day with a contact UA."
    ),
    "overpass.private.coffee": "same as overpass-api.de (/api/interpreter query API)",
    "overpass.osm.ch": "same as overpass-api.de (/api/interpreter query API)",
    "overpass.kumi.systems": "same as overpass-api.de (/api/interpreter query API)",
}

POWER_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kW|MW|kVA)\b", re.IGNORECASE)


def _power_kw(tags: dict) -> float | None:
    best = None
    for key, value in tags.items():
        if not isinstance(value, str):
            continue
        if "output" not in key and key not in {"power", "charging_station:output"}:
            continue
        for m in POWER_RE.finditer(value):
            val = float(m.group(1).replace(",", "."))
            unit = m.group(2).upper()
            kw = val * 1000 if unit in {"MW"} else val
            if 3 <= kw <= 3000:
                best = kw if best is None else max(best, kw)
    return best


def _connectors(tags: dict) -> list[str] | None:
    out = set()
    for key, value in tags.items():
        if key.startswith("socket:") and value not in {"no", ""}:
            out.add(key.split(":", 1)[1].split(":")[0])
        if key == "socket:mcs":
            out.add("MCS")
    return sorted(out) or None


def normalise(el: dict) -> dict | None:
    tags = el.get("tags") or {}
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lon is None:
        return None
    oid = f"{el.get('type','node')}/{el.get('id')}"
    power = _power_kw(tags)
    access_raw = (tags.get("access") or "").lower()
    access = {
        "private": "private",
        "no": "private",
        "customers": "customers",
        "permit": "customers",
    }.get(access_raw, access_raw or "unknown")
    name = tags.get("name") or tags.get("operator") or "Laadlocatie (OSM)"
    hgv = (tags.get("hgv") in {"yes", "designated", "only"}) or (
        tags.get("socket:mcs") is not None
    )
    n_points = tags.get("capacity")
    try:
        n_points = int(n_points) if n_points else None
    except ValueError:
        n_points = None
    return {
        "signal_id": f"osm:{oid}",
        "source": "osm",
        "source_key": oid,
        "kind": "osm",
        "name": str(name)[:200],
        "operator": tags.get("operator"),
        "address": " ".join(
            filter(None, [tags.get("addr:street"), tags.get("addr:housenumber")])
        ) or None,
        "postcode": (tags.get("addr:postcode") or "").replace(" ", "") or None,
        "city": tags.get("addr:city"),
        "lon": float(lon),
        "lat": float(lat),
        "bag_id": None,
        "power_kw": power,
        "n_points": n_points,
        "connector_types": _connectors(tags),
        "hgv_capable": hgv,
        "access": access,
        "status_hint": "operational",
        "text_excerpt": None,
        "doc_url": f"https://www.openstreetmap.org/{oid}",
        "observed_at": None,
        "raw_state": {"id": oid, "tags": tags, "coordinates": [float(lon), float(lat)]},
        "content_hash": hashlib.sha256(
            json.dumps([oid, sorted(tags.items()), power, hgv, access],
                       sort_keys=True, default=str).encode()
        ).hexdigest(),
    }


# Minimum power that can matter for a heavy-duty vehicle. Below this an OSM
# node is a car charger no matter what access tag it carries.
MIN_TRUCK_KW = 50.0


def _truck_relevant(sig: dict) -> bool:
    """Could this charge point serve a truck?

    OSM tags access=private|customers on car chargers constantly, so access alone
    says nothing. Require an explicit truck signal: hgv tag, an MCS connector, or
    at least 50 kW.
    """
    if sig.get("hgv_capable"):
        return True
    connectors = {str(c).upper() for c in (sig.get("connector_types") or [])}
    if "MCS" in connectors:
        return True
    return float(sig.get("power_kw") or 0) >= MIN_TRUCK_KW


def run(store: Store, settings: Settings) -> dict:
    run_id = store.start_run("ingest", "osm")
    stats = {"elements": 0, "kept": 0, "new": 0, "updated": 0, "unchanged": 0,
             "mirror": None, "duration_s": None, "attempts": []}
    last_err: Exception | None = None
    # One attempt per mirror: the rotation IS the retry. Retrying a slow mirror
    # multiplies the wait without adding a chance of success.
    osm_settings = replace(settings, http_timeout=OVERPASS_TIMEOUT, max_retries=1)
    data: dict | None = None
    try:
        for mirror in MIRRORS:
            host = mirror.split("/")[2]
            started = time.monotonic()
            try:
                with Fetcher(osm_settings, store=store, run_id=run_id, source="osm",
                            robots_exempt=OVERPASS_EXEMPT) as fetcher:
                    resp = fetcher.get(mirror, method="POST", data={"data": QUERY})
                elapsed = round(time.monotonic() - started, 1)
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                payload = json.loads(resp.content)
                elements = payload.get("elements") or []
                if not elements:
                    # overpass.osm.ch answers 200 in 0.2s with an empty body.
                    # Treating that as success would silently wipe every
                    # OSM-derived signal from the database.
                    raise RuntimeError("empty result (HTTP 200 but 0 elements)")
                data = payload
                stats["mirror"] = mirror
                stats["duration_s"] = elapsed
                stats["attempts"].append(
                    {"mirror": host, "ok": True, "s": elapsed, "n": len(elements)})
                break
            except Exception as exc:  # try the next mirror
                last_err = exc
                stats["attempts"].append(
                    {"mirror": host, "ok": False,
                     "s": round(time.monotonic() - started, 1),
                     "err": str(exc)[:120]})
        if data is None:
            raise RuntimeError(f"all Overpass mirrors failed: {last_err}")

        elements = data.get("elements") or []
        stats["elements"] = len(elements)
        for el in elements:
            sig = normalise(el)
            if sig is None:
                continue
            if not _truck_relevant(sig):
                stats["skipped_car_only"] = stats.get("skipped_car_only", 0) + 1
                continue
            stats["kept"] += 1
            stats[store.upsert_signal(sig)] += 1
        store.record_health("osm", True, stats["kept"])
        store.finish_run(run_id, status="ok", records_in=stats["elements"],
                         records_out=stats["kept"], note=as_json(stats))
        return stats
    except Exception as exc:
        store.record_health("osm", False, 0, error=str(exc)[:500])
        store.alert("source_failure", f"osm ingest failed: {exc}", source="osm")
        store.finish_run(run_id, status="failed", error=str(exc)[:2000])
        raise
