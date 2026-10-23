"""Ingest: Depotcharge fleet map (snapshot based).

The fleet map sits behind a login and offers no public API, and the host must not
be hammered. So this ingester never touches the network. It reads a snapshot of
two endpoints that were captured once from an authenticated browser session:

    data/depotcharge/locations.json     fleets/{fleet}/map-locations  (all pages)
    data/depotcharge/chargepoints.json  fleets/{fleet}/map-locations/{id}/chargepoints

To refresh, log in and re-capture those two files (see project_state.md). The
scheduled cycle only reads the snapshot, so a login wall, an expired token or a
rate limit can never break the daily run.

Why this source matters: the chargepoint payload carries `truckCompatibility`
(PULL_THROUGH / REVERSE_OUT / UNCOUPLE / UNCOUPLE_RECOMMEND / NOT_COMPATIBLE) and
per-connector `maxElectricPower`. That is an explicit heavy-duty signal from the
operator's own records, not an inference from a name or a tag.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import date

from ..config import Settings
from ..store import Store, as_json

# Only the Netherlands: this map is a Dutch private-depot product. The same feed
# carries 184 German/Swiss/Spanish/Italian sites; add the country codes here if a
# wider map is ever wanted.
NL_COUNTRIES = {"Nederland", "Niederlande", "Netherlands", "NL"}

# The fleet feed is mostly German and Swiss. Those rows are wanted on the map, so
# the gate is now "a country we can name", not "in the Netherlands". Labels arrive
# localised (Deutschland / Suisse / Spanien), so map both spellings to a code.
COUNTRY_CODE = {
    "Nederland": "NL", "Niederlande": "NL", "Netherlands": "NL",
    "Deutschland": "DE", "Germany": "DE",
    "Schweiz": "CH", "Suisse": "CH", "Switzerland": "CH",
    "Spanien": "ES", "Espana": "ES", "España": "ES", "Spain": "ES",
    "Italien": "IT", "Italia": "IT", "Italy": "IT",
    "Osterreich": "AT", "Österreich": "AT", "Austria": "AT",
    "Belgie": "BE", "België": "BE", "Belgique": "BE", "Belgium": "BE",
    "Luxemburg": "LU", "Luxembourg": "LU",
    "Liechtenstein": "LI",
    "Frankreich": "FR", "France": "FR",
    "Danemark": "DK", "Dänemark": "DK", "Denmark": "DK",
    "Polen": "PL", "Poland": "PL",
}
COUNTRY_OK = set(COUNTRY_CODE)

# Truck ratings that mean the bay physically suits an HGV. NOT_COMPATIBLE does not.
TRUCK_OK = {"PULL_THROUGH", "REVERSE_OUT", "UNCOUPLE", "UNCOUPLE_RECOMMEND"}

MIN_TRUCK_KW = 50.0

CONNECTOR_MAP = {
    "IEC_62196_T2_COMBO": "CCS",
    "IEC_62196_T2": "TYPE2",
    "CHADEMO": "CHAdeMO",
    "MCS": "MCS",
    "TESLA_S": "TESLA",
}


def _snapshot_dir(settings: Settings) -> pathlib.Path:
    return pathlib.Path(settings.depotcharge_dir)


def _max_power(connectors: list[dict]) -> float | None:
    powers = [float(c.get("maxElectricPower") or 0) for c in connectors]
    powers = [p for p in powers if 0 < p <= 3000]
    return max(powers) if powers else None


def _connector_types(connectors: list[dict]) -> list[str] | None:
    out = set()
    for c in connectors:
        raw = (c.get("connectorType") or "").upper()
        if "MCS" in raw:
            out.add("MCS")
            continue
        out.add(CONNECTOR_MAP.get(raw, raw or "UNKNOWN"))
    return sorted(out) or None


def normalise(row: dict, chargepoints: list[dict], fleet_url: str) -> dict | None:
    """Turn one map-locations row + its chargepoints into a signal dict."""
    loc = row.get("location") or {}
    addr = loc.get("address") or {}
    country = addr.get("country")
    if country not in COUNTRY_OK:
        return None

    lon = (loc.get("coordinates") or {}).get("longitude")
    lat = (loc.get("coordinates") or {}).get("latitude")
    if lon is None or lat is None:
        return None

    connectors = [c for cp in chargepoints for c in (cp.get("connectors") or [])]
    truck_ratings = {c.get("truckCompatibility") for c in connectors if c.get("truckCompatibility")}
    hgv = bool(truck_ratings & TRUCK_OK)
    power = _max_power(connectors) or (
        loc.get("expectedChargePointPowerLevel") or None
    )
    access = {"PRIVATE": "private", "PUBLIC": "public"}.get(
        (loc.get("accessType") or "").upper(), "unknown")
    operational = (loc.get("operationalStatus") or "").upper() == "OPERATIONAL"

    # Relevance gate. A published location is only worth a row when there is a
    # reason to think a truck could charge there: an explicit truck rating, a
    # private yard, or enough power to matter. This drops the planned public
    # fillers that carry no connectors at all.
    has_plan = bool(loc.get("plannedChargePoints"))
    if not (
        hgv
        or access in ("private", "public", "semi_public")
        or (power or 0) >= MIN_TRUCK_KW
        or has_plan
    ):
        return None

    # Tier input: "operational" is reserved for sites the feed actually backs with
    # hardware. An operational flag with no connectors and no power is a claim we
    # cannot check, so it is scored like a plan.
    status = "operational" if operational and (hgv or (power or 0) >= MIN_TRUCK_KW) else "planned"

    lid = loc["id"]
    raw_state = {
        "id": lid,
        "endpoint": fleet_url,
        "access_type": loc.get("accessType"),
        "operational_status": loc.get("operationalStatus"),
        "opening_date": loc.get("openingDate"),
        "visibility": loc.get("visibility"),
        "country": COUNTRY_CODE.get(country),
        "country_raw": country,
        "truck_ratings": sorted(truck_ratings),
        "connectors": [
            {
                "type": c.get("connectorType"),
                "kw": c.get("maxElectricPower"),
                "truck": c.get("truckCompatibility"),
            }
            for c in connectors
        ],
    }
    return {
        "signal_id": f"depotcharge:{lid}",
        "source": "depotcharge",
        "source_key": lid,
        "kind": "depotcharge",
        "name": str(loc.get("name") or "Laadlocatie (Depotcharge)")[:200],
        "operator": (loc.get("organization") or {}).get("name"),
        "address": addr.get("street"),
        "postcode": (addr.get("postalCode") or "").replace(" ", "") or None,
        "city": addr.get("city"),
        "country": COUNTRY_CODE.get(country),
        "lon": float(lon),
        "lat": float(lat),
        "bag_id": None,
        "power_kw": power,
        "n_points": len(connectors) or None,
        "connector_types": _connector_types(connectors),
        "hgv_capable": hgv,
        "access": access,
        "status_hint": status,
        "text_excerpt": None,
        "doc_url": fleet_url,
        "observed_at": date.today(),
        "raw_state": raw_state,
        "content_hash": hashlib.sha256(
            json.dumps(
                [lid, power, hgv, access, status, len(connectors), sorted(truck_ratings)],
                sort_keys=True, default=str,
            ).encode()
        ).hexdigest(),
    }


def run(store: Store, settings: Settings) -> dict:
    run_id = store.start_run("ingest", "depotcharge")
    stats = {"rows": 0, "kept": 0, "new": 0, "updated": 0, "unchanged": 0,
             "skipped_country": 0, "skipped_irrelevant": 0, "skipped_nogeom": 0}
    snap = _snapshot_dir(settings)
    loc_path = snap / "locations.json"
    cp_path = snap / "chargepoints.json"
    if not loc_path.exists():
        # A missing snapshot must never fail the scheduled cycle: the rest of the
        # pipeline is still worth publishing. Report it and move on.
        stats["skipped"] = f"no snapshot at {loc_path}"
        store.record_health("depotcharge", False, 0)
        store.finish_run(run_id, status="skipped", records_in=0, records_out=0,
                         note=as_json(stats))
        return stats

    rows = json.loads(loc_path.read_text())
    cps = json.loads(cp_path.read_text()) if cp_path.exists() else {}
    fleet_url = settings.depotcharge_fleet_url
    stats["rows"] = len(rows)

    for row in rows:
        loc = row.get("location") or {}
        country = (loc.get("address") or {}).get("country")
        lid = loc.get("id")
        sig = normalise(row, cps.get(lid, []), fleet_url)
        if sig is None:
            if country not in COUNTRY_OK:
                stats["skipped_country"] += 1
            elif not (loc.get("coordinates") or {}).get("latitude"):
                stats["skipped_nogeom"] += 1
            else:
                stats["skipped_irrelevant"] += 1
            continue
        stats["kept"] += 1
        stats[store.upsert_signal(sig)] += 1

    store.record_health("depotcharge", True, stats["kept"])
    store.finish_run(run_id, status="ok", records_in=stats["rows"],
                     records_out=stats["kept"], note=as_json(stats))
    return stats
