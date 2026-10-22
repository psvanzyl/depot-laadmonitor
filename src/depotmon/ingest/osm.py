"""Ingest: OpenStreetMap Overpass — truck-relevant and non-public charge points.

Only the subset that OSM is uniquely good at is queried: hgv tagging, MCS sockets and
private/customer-only access. Public forecourts are already covered (better) by NDW.
"""

from __future__ import annotations

import hashlib
import json
import re

from ..config import Settings
from ..fetch import Fetcher
from ..store import Store, as_json

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="NL"][admin_level=2]->.nl;
(
  nwr["amenity"="charging_station"]["hgv"](area.nl);
  nwr["amenity"="charging_station"]["socket:mcs"](area.nl);
  nwr["amenity"="charging_station"]["access"~"^(private|customers|no|permit)$"](area.nl);
);
out center tags;
""".strip()

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


def run(store: Store, settings: Settings) -> dict:
    run_id = store.start_run("ingest", "osm")
    stats = {"elements": 0, "kept": 0, "new": 0, "updated": 0, "unchanged": 0,
             "mirror": None}
    last_err: Exception | None = None
    try:
        for mirror in MIRRORS:
            try:
                with Fetcher(settings, store=store, run_id=run_id, source="osm") as fetcher:
                    resp = fetcher.get(mirror, method="POST", data={"data": QUERY})
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                data = json.loads(resp.content)
                stats["mirror"] = mirror
                break
            except Exception as exc:  # try the next mirror
                last_err = exc
        else:
            raise RuntimeError(f"all Overpass mirrors failed: {last_err}")

        elements = data.get("elements") or []
        stats["elements"] = len(elements)
        for el in elements:
            sig = normalise(el)
            if sig is None:
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
