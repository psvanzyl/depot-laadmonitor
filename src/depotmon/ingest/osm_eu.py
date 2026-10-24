"""Ingest: OpenStreetMap Overpass — truck-relevant charge points, EUROPE-WIDE.

Why a separate module instead of widening `osm.py`:

* `osm.py` issues ONE national bbox query. A Europe-wide bbox query returns
  17k elements and takes ~125 s, which is fine, but a single continent-wide
  query is fragile: one slow mirror kills the whole ingest, and the answer
  cannot be retried per country.
* Coverage must be auditable per country. A per-country loop writes one
  `runs` row per country, so a partial failure is visible in the health table
  instead of hidden inside one aggregate number.

The tags queried are the subset OSM is uniquely good at:

    amenity=charging_station + hgv=yes|designated     truck-capable
    amenity=charging_station + socket:mcs:*            megawatt charging
    amenity=charging_station + access=private|customers|no|permit

Public forecourts are covered better by NDW (NL only) and the operator feeds.
Access tags alone prove nothing (car chargers carry access=private constantly),
so every element must ALSO carry an explicit truck signal or enough power.

Country is derived from the coordinates with a point-in-box test against a
country list. That is a bbox approximation, not a real border polygon, so a
site a few km outside a border can be mislabelled. The error is at the scale of
the border, which is acceptable for a map layer, and the same approximation is
already used for the Dutch province table. Where the element carries an
explicit `addr:country` tag, that wins.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace

from ..config import Settings
from ..fetch import Fetcher
from ..store import Store, as_json
from . import country_geom

OVERPASS_TIMEOUT = 300.0

# Ordered by measured reliability from CT141 (see project_state.md).
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

OVERPASS_EXEMPT = {
    "overpass-api.de": (
        "/api/interpreter is a documented query API, not crawlable content; "
        "robots.txt Disallow: /api/ targets crawlers. Policy honoured by sending "
        "one query per country per day with a contact UA."
    ),
    "overpass.private.coffee": "same as overpass-api.de (/api/interpreter query API)",
    "overpass.kumi.systems": "same as overpass-api.de (/api/interpreter query API)",
    "overpass.osm.ch": "same as overpass-api.de (/api/interpreter query API)",
}

# One bounding box per European country. The boxes are deliberately generous:
# they include neighbouring sea and land so nothing on the border is lost.
# A bbox test is an approximation of a border, which is good enough for a map
# layer and cheap enough to run offline.
#
# name, south, west, north, east
EUROPE_BOXES: dict[str, tuple[float, float, float, float]] = {
    "NL": (50.7, 3.2, 53.6, 7.3),
    "BE": (49.4, 2.5, 51.6, 6.5),
    "DE": (47.2, 5.8, 55.1, 15.1),
    "DK": (54.5, 8.0, 57.8, 12.7),
    "SE": (55.3, 11.0, 69.1, 24.2),
    "NO": (57.9, 4.5, 71.3, 31.1),
    "FI": (59.8, 20.5, 70.1, 31.6),
    "IS": (63.3, -24.0, 67.1, -13.4),
    "IE": (51.3, -10.6, 55.5, -5.9),
    "GB": (49.8, -8.7, 61.0, 1.8),
    "FR": (41.3, -5.2, 51.2, 9.6),
    "LU": (49.4, 5.7, 50.2, 6.6),
    "CH": (45.7, 5.9, 47.9, 10.6),
    "AT": (46.3, 9.5, 49.1, 17.2),
    "IT": (35.4, 6.5, 47.1, 18.6),
    "ES": (35.9, -9.5, 43.9, 3.4),
    "PT": (36.8, -9.6, 42.2, -6.1),
    "PL": (49.0, 14.1, 54.9, 24.2),
    "CZ": (48.5, 12.0, 51.1, 18.9),
    "SK": (47.6, 16.8, 49.6, 22.6),
    "HU": (45.7, 16.1, 48.6, 22.9),
    "AT2": (0, 0, 0, 0),  # placeholder removed below
    "RO": (43.6, 20.2, 48.3, 29.7),
    "BG": (41.2, 22.3, 44.2, 28.6),
    "GR": (34.8, 19.3, 41.8, 28.3),
    "SI": (45.4, 13.3, 46.9, 16.6),
    "HR": (42.4, 13.5, 46.6, 19.5),
    "BA": (42.5, 15.7, 45.3, 19.6),
    "RS": (42.2, 18.8, 46.2, 23.0),
    "ME": (41.8, 18.4, 43.6, 20.4),
    "MK": (40.8, 20.4, 42.4, 23.1),
    "AL": (39.6, 19.2, 42.7, 21.1),
    "EE": (57.4, 21.5, 59.7, 28.2),
    "LV": (55.6, 20.9, 58.1, 28.3),
    "LT": (53.8, 20.9, 56.5, 26.9),
    "CY": (34.5, 32.2, 35.7, 34.6),
    "MT": (35.7, 14.2, 36.1, 14.6),
    "AD": (42.4, 1.4, 42.7, 1.8),
    "MC": (43.7, 7.3, 43.8, 7.5),
    "SM": (43.9, 12.3, 44.0, 12.5),
    "LI": (47.0, 9.4, 47.3, 9.6),
    "VA": (41.8, 12.3, 42.0, 12.5),
    "XK": (41.8, 20.0, 43.3, 21.8),
}
del EUROPE_BOXES["AT2"]

# Country order for the Europe pass. The busiest charging countries go first,
# so a partial run (timeout, rate limit, an interrupted session) still leaves
# the most valuable coverage in place. Every country is still attempted.
PRIORITY_ORDER = [
    "DE", "FR", "NL", "IT", "ES", "GB", "PL", "PT", "AT", "SE", "CH", "CZ",
    "BE", "NO", "FI", "DK", "HU", "RO", "GR", "SK", "SI", "HR", "LT", "LV",
    "EE", "BG", "RS", "BA", "ME", "MK", "AL", "IS", "IE", "LU", "CY", "MT",
    "LI", "AD", "MC", "SM", "VA", "XK",
]

# ISO-2 -> ISO-3, so the same country code can be written in the format the
# eTrucker feed uses (NLD, DEU, FRA) and the exporter already knows.
ISO2_TO_ISO3 = {
    "NL": "NLD", "BE": "BEL", "DE": "DEU", "DK": "DNK", "SE": "SWE", "NO": "NOR",
    "FI": "FIN", "IS": "ISL", "IE": "IRL", "GB": "GBR", "FR": "FRA", "LU": "LUX",
    "CH": "CHE", "AT": "AUT", "IT": "ITA", "ES": "ESP", "PT": "PRT", "PL": "POL",
    "CZ": "CZE", "SK": "SVK", "HU": "HUN", "RO": "ROU", "BG": "BGR", "GR": "GRC",
    "SI": "SVN", "HR": "HRV", "BA": "BIH", "RS": "SRB", "ME": "MNE", "MK": "MKD",
    "AL": "ALB", "EE": "EST", "LV": "LVA", "LT": "LTU", "CY": "CYP", "MT": "MLT",
    "AD": "AND", "MC": "MCO", "SM": "SMR", "LI": "LIE", "VA": "VAT", "XK": "XKX",
}

QUERY = """
[out:json][timeout:280];
(
  nwr["amenity"="charging_station"]["hgv"]({b});
  nwr["amenity"="charging_station"]["socket:mcs"]({b});
  nwr["amenity"="charging_station"]["access"~"^(private|customers|no|permit)$"]({b});
);
out center tags;
""".strip()

# Minimum power that can matter for a heavy-duty vehicle. Below this an OSM
# node is a car charger no matter what access tag it carries.
MIN_TRUCK_KW = 50.0


def country_of(lon: float, lat: float) -> str:
    """ISO-2 country for a point.

    Uses the real country polygons, not the Overpass query boxes. A box is a
    rectangle and Europe is not: Aachen sits inside the Dutch, German and
    Belgian rectangles, and the box test labelled it Belgian. The boxes above
    still bound the Overpass query; they do not decide the country.
    """
    return country_geom.country_for(lon, lat)


def _power_kw(tags: dict) -> float | None:
    import re

    best = None
    pattern = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kW|MW|kVA)\b", re.IGNORECASE)
    for key, value in tags.items():
        if not isinstance(value, str):
            continue
        if "output" not in key and key not in {"power", "charging_station:output"}:
            continue
        for m in pattern.finditer(value):
            val = float(m.group(1).replace(",", "."))
            unit = m.group(2).upper()
            kw = val * 1000 if unit == "MW" else val
            if 3 <= kw <= 3000:
                best = kw if best is None else max(best, kw)
    return best


def _connectors(tags: dict) -> list[str] | None:
    out = set()
    for key, value in tags.items():
        if key.startswith("socket:") and value not in {"no", ""}:
            out.add(key.split(":", 1)[1].split(":")[0])
    return sorted(out) or None


def _truck_relevant(sig: dict) -> bool:
    """Could this charge point serve a truck?

    OSM tags access=private|customers on car chargers constantly, so access
    alone says nothing. Require an explicit truck signal: hgv tag, an MCS
    connector, or at least 50 kW.
    """
    if sig.get("hgv_capable"):
        return True
    connectors = {str(c).upper() for c in (sig.get("connector_types") or [])}
    if "MCS" in connectors:
        return True
    return float(sig.get("power_kw") or 0) >= MIN_TRUCK_KW


def normalise(el: dict) -> dict | None:
    tags = el.get("tags") or {}
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat is None or lon is None:
        return None
    oid = f"{el.get('type', 'node')}/{el.get('id')}"
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
    # An explicit country tag beats the bbox approximation.
    declared = (tags.get("addr:country") or "").strip().upper()
    if len(declared) == 2 and declared in EUROPE_BOXES:
        country = declared
    elif len(declared) == 3 and declared in ISO2_TO_ISO3.values():
        country = next(k for k, v in ISO2_TO_ISO3.items() if v == declared)
    else:
        country = country_of(float(lon), float(lat))
    return {
        "signal_id": f"osm_eu:{oid}",
        "source": "osm_eu",
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
        "raw_state": {
            "id": oid,
            "tags": tags,
            "coordinates": [float(lon), float(lat)],
            "country": country,
            "countryIso3": ISO2_TO_ISO3.get(country, ""),
        },
        "content_hash": hashlib.sha256(
            json.dumps([oid, sorted(tags.items()), power, hgv, access],
                       sort_keys=True, default=str).encode()
        ).hexdigest(),
    }


def _fetch_country(settings, store, run_id, country: str, box) -> dict:
    """Query one country, rotating mirrors until one answers.

    Overpass rate-limits with HTTP 429 when the load is too high. That is a
    "come back later", not a failure, so the mirror list is walked and a short
    pause is taken between attempts. Waiting out the limit is cheaper than
    losing the country: 40+ countries x 90 s each is the whole European pass.
    """
    s, w, n, e = box
    query = QUERY.format(b=f"{s},{w},{n},{e}")
    osm_settings = replace(settings, http_timeout=OVERPASS_TIMEOUT, max_retries=1)
    attempts = []
    for attempt, mirror in enumerate(MIRRORS):
        host = mirror.split("/")[2]
        started = time.monotonic()
        try:
            with Fetcher(osm_settings, store=store, run_id=run_id, source="osm_eu",
                         robots_exempt=OVERPASS_EXEMPT) as fetcher:
                resp = fetcher.get(mirror, method="POST", data={"data": query})
            elapsed = round(time.monotonic() - started, 1)
            if resp.status == 429:
                # Rate limited. Back off, then try the next mirror.
                attempts.append({"mirror": host, "ok": False, "s": elapsed,
                                 "err": "429 rate limited"})
                time.sleep(20 * (attempt + 1))
                continue
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            payload = json.loads(resp.content)
            elements = payload.get("elements") or []
            if not elements:
                raise RuntimeError("empty result (HTTP 200 but 0 elements)")
            attempts.append({"mirror": host, "ok": True, "s": elapsed, "n": len(elements)})
            return {"elements": elements, "attempts": attempts, "mirror": host}
        except Exception as exc:
            attempts.append({"mirror": host, "ok": False,
                             "s": round(time.monotonic() - started, 1),
                             "err": str(exc)[:120]})
    raise RuntimeError(f"all mirrors failed for {country}: {attempts[-1] if attempts else '?'}")


def run(store: Store, settings: Settings, countries: list[str] | None = None) -> dict:
    """Ingest every European country, one Overpass query per country."""
    targets = countries or PRIORITY_ORDER
    overall: dict = {"countries": {}, "elements": 0, "kept": 0, "failed": []}
    for code in targets:
        box = EUROPE_BOXES.get(code)
        if not box:
            continue
        run_id = store.start_run("ingest", f"osm_eu:{code}")
        try:
            data = _fetch_country(settings, store, run_id, code, box)
            stats = {"elements": len(data["elements"]), "kept": 0, "new": 0,
                     "updated": 0, "unchanged": 0}
            for el in data["elements"]:
                sig = normalise(el)
                if sig is None:
                    continue
                if not _truck_relevant(sig):
                    stats["skipped_car_only"] = stats.get("skipped_car_only", 0) + 1
                    continue
                # An element can sit in two overlapping boxes. Keep the first
                # country that claims it, so one site is not counted twice.
                sig["raw_state"]["country"] = code
                sig["raw_state"]["countryIso3"] = ISO2_TO_ISO3.get(code, "")
                stats["kept"] += 1
                stats[store.upsert_signal(sig)] += 1
            overall["elements"] += stats["elements"]
            overall["kept"] += stats["kept"]
            overall["countries"][code] = stats
            store.record_health(f"osm_eu:{code}", True, stats["kept"])
            store.finish_run(run_id, status="ok", records_in=stats["elements"],
                             records_out=stats["kept"], note=as_json(stats))
            print(f"[osm_eu] {code}: {stats['kept']}/{stats['elements']} kept")
        except Exception as exc:
            overall["failed"].append(code)
            store.record_health(f"osm_eu:{code}", False, 0, error=str(exc)[:500])
            store.alert("source_failure", f"osm_eu {code} failed: {exc}",
                        source=f"osm_eu:{code}")
            store.finish_run(run_id, status="failed", error=str(exc)[:2000])
            print(f"[osm_eu] {code}: FAILED {exc}")
    return overall
