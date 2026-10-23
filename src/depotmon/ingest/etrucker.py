"""Ingest: eTrucker heavy-vehicle charging locations (snapshot based).

eTrucker publishes station data through a Firebase project (`etrucker-prod`).
Anonymous sign-in with the app's own public client key can read the `location`
collection, which is how the phone app shows markers before anyone logs in.

That payload is one document per European charging site with a structured
`heavy_vehicles` block. This ingester never touches the network: it reads a
snapshot that `scripts/etrucker_snapshot.py` reduced to HDV-relevant records, so
an expired key, a changed security rule or a rate limit can never break the
daily cycle.

Why this source matters: `heavy_vehicles_dedicated`, `_drive_through`,
`_rigid_vehicles`, `_trailer_accessibility`, `_dangerous_goods`,
`_overnight_parking` and the N2/N3 restriction codes are the operator-side
statement that a truck fits. That is explicit evidence, not a name guess.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import date

from ..config import Settings
from ..store import Store, as_json

MIN_TRUCK_KW = 50.0

# Flag keys that mean "a heavy vehicle physically fits here".
TRUCK_FLAGS = (
    "heavy_vehicles_dedicated",
    "heavy_vehicles_drive_through",
    "heavy_vehicles_rigid_vehicles",
    "heavy_vehicles_trailer_accessibility",
    "heavy_vehicles_dangerous_goods",
    "heavy_vehicles_overnight_parking",
)
HGV_CODES = {"N2", "N3", "N2_N3"}


def _snapshot_dir(settings: Settings) -> pathlib.Path:
    return pathlib.Path(settings.etrucker_dir)


def _hgv(row: dict) -> bool:
    flags = row.get("hgv_flags") or {}
    if any(flags.get(flag) for flag in TRUCK_FLAGS):
        return True
    if set(row.get("hgv_restrictions") or []) & HGV_CODES:
        return True
    return "MCS" in (row.get("connectors") or [])


def normalise(row: dict, source_url: str) -> dict | None:
    """Turn one snapshot row into a signal dict, or None if it is not relevant."""
    lat, lon = row.get("lat"), row.get("lon")
    if lat is None or lon is None:
        return None

    hgv = _hgv(row)
    power = row.get("max_kw")
    if not hgv:
        return None

    access = row.get("access") or "unknown"
    name = (row.get("name") or "Laadlocatie (eTrucker)")[:200]
    country = row.get("country")
    site_id = row.get("id")

    raw_state = {
        "id": site_id,
        "endpoint": source_url,
        "country": country,
        "location_type": row.get("location_type"),
        "hgv_flags": row.get("hgv_flags"),
        "hgv_restrictions": row.get("hgv_restrictions"),
        "connectors": row.get("connectors"),
        "max_kw": power,
        "n_points": row.get("n_points"),
        "last_updated": row.get("last_updated"),
    }
    return {
        "signal_id": f"etrucker:{site_id}",
        "source": "etrucker",
        "source_key": site_id,
        "kind": "etrucker",
        "name": name,
        "operator": row.get("operator"),
        "address": row.get("address"),
        "postcode": row.get("postcode"),
        "city": row.get("city"),
        "country": country,
        "lon": float(lon),
        "lat": float(lat),
        "bag_id": None,
        "power_kw": power,
        "n_points": row.get("n_points"),
        "connector_types": row.get("connectors"),
        "hgv_capable": hgv,
        "access": access,
        # The feed carries sites that are already in service; there is no
        # "planned" state in this snapshot.
        "status_hint": "operational" if row.get("last_updated") else "unknown",
        "text_excerpt": None,
        "doc_url": source_url,
        "observed_at": date.today(),
        "raw_state": raw_state,
        "content_hash": hashlib.sha256(
            json.dumps(
                [site_id, power, hgv, access, row.get("connectors"),
                 row.get("n_points"), row.get("hgv_flags"), row.get("hgv_restrictions")],
                sort_keys=True, default=str,
            ).encode()
        ).hexdigest(),
    }


def run(store: Store, settings: Settings) -> dict:
    run_id = store.start_run("ingest", "etrucker")
    stats = {"rows": 0, "kept": 0, "new": 0, "updated": 0, "unchanged": 0,
             "skipped_irrelevant": 0, "skipped_nogeom": 0}

    path = _snapshot_dir(settings) / "locations.json"
    if not path.exists():
        # A missing snapshot must never fail the scheduled cycle.
        stats["skipped"] = f"no snapshot at {path}"
        store.record_health("etrucker", False, 0)
        store.finish_run(run_id, status="skipped", records_in=0, records_out=0,
                         note=as_json(stats))
        return stats

    rows = json.loads(path.read_text())
    stats["rows"] = len(rows)
    source_url = settings.etrucker_source_url

    for row in rows:
        sig = normalise(row, source_url)
        if sig is None:
            if row.get("lat") is None or row.get("lon") is None:
                stats["skipped_nogeom"] += 1
            else:
                stats["skipped_irrelevant"] += 1
            continue
        stats["kept"] += 1
        stats[store.upsert_signal(sig)] += 1

    store.record_health("etrucker", True, stats["kept"])
    store.finish_run(run_id, status="ok", records_in=stats["rows"],
                     records_out=stats["kept"], note=as_json(stats))
    return stats
