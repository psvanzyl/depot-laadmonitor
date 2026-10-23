"""Ingest: NDW charging point locations (OCPI register, daily GeoJSON)."""

from __future__ import annotations

import hashlib
import json

from ..config import Settings
from ..fetch import Fetcher
from ..store import Store, as_json

URL = "https://opendata.ndw.nu/charging_point_locations.geojson.gz"

# Data-quality guards (see SOURCES.md #2): the register contains impossible values.
MAX_PLAUSIBLE_KW = 3000.0
HGV_POWER_THRESHOLD_KW = 350.0
DEFAULT_MIN_KW = 100.0


def _power_kw(props: dict) -> float | None:
    best = None
    for av in props.get("availabilities") or []:
        raw = av.get("power_max")
        if not raw:
            continue
        try:
            kw = float(raw) / 1000.0
        except (TypeError, ValueError):
            continue
        if kw <= 0 or kw > MAX_PLAUSIBLE_KW:
            continue
        best = kw if best is None else max(best, kw)
    return best


def _n_points(props: dict) -> int | None:
    total = 0
    for av in props.get("availabilities") or []:
        try:
            total += int(av.get("total") or 0)
        except (TypeError, ValueError):
            continue
    return total or None


def _connectors(props: dict) -> list[str] | None:
    found = {
        av.get("connector_type")
        for av in (props.get("availabilities") or [])
        if av.get("connector_type")
    }
    return sorted(found) or None


def normalise(feature: dict) -> dict | None:
    props = feature.get("properties") or {}
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None
    lon, lat = float(coords[0]), float(coords[1])
    power = _power_kw(props)
    if power is None:
        return None
    # The NDW open-data dump puts the unique id at the FEATURE top level
    # ("NL-<CPO>-<uuid>"); properties only carry "cpo_id" (the operator code).
    # Reading properties["id"] silently dropped every single feature.
    oid = str(feature.get("id") or props.get("id") or "")
    if not oid:
        return None
    access = "public" if props.get("open") else "unknown"
    n_points = _n_points(props)
    connectors = _connectors(props)
    return {
        "signal_id": f"ndw:{oid}",
        "source": "ndw",
        "source_key": oid,
        "kind": "chargepoint",
        "name": (props.get("address") or "Laadlocatie")[:200],
        "operator": props.get("operator_name") or props.get("suboperator_name"),
        "address": props.get("address"),
        "postcode": None,
        "city": props.get("city"),
        "lon": lon,
        "lat": lat,
        "bag_id": None,
        "power_kw": power,
        "n_points": n_points,
        "connector_types": connectors,
        "hgv_capable": power >= HGV_POWER_THRESHOLD_KW,
        "access": access,
        # presence in the CPO register means the hardware exists and is in service
        "status_hint": "operational",
        "text_excerpt": None,
        "doc_url": URL,
        "observed_at": None,
        "raw_state": {
            "id": oid,
            "operator": props.get("operator_name"),
            "owner": props.get("owner_name"),
            "suboperator": props.get("suboperator_name"),
            "open": props.get("open"),
            "availabilities": props.get("availabilities"),
            "coordinates": [lon, lat],
        },
        "content_hash": hashlib.sha256(
            json.dumps([oid, power, n_points, connectors, props.get("open")],
                       sort_keys=True).encode()
        ).hexdigest(),
    }


def run(store: Store, settings: Settings, *, min_kw: float = DEFAULT_MIN_KW) -> dict:
    run_id = store.start_run("ingest", "ndw")
    stats = {"features": 0, "kept": 0, "new": 0, "updated": 0, "unchanged": 0}
    try:
        with Fetcher(settings, store=store, run_id=run_id, source="ndw") as fetcher:
            resp = fetcher.get(URL)
            if resp.status != 200:
                raise RuntimeError(f"NDW HTTP {resp.status}")
            data = json.loads(resp.content)
        features = data.get("features") or []
        stats["features"] = len(features)
        for feat in features:
            sig = normalise(feat)
            if sig is None or (sig["power_kw"] or 0) < min_kw:
                continue
            stats["kept"] += 1
            stats[store.upsert_signal(sig)] += 1
        store.record_health("ndw", True, stats["kept"])
        store.finish_run(run_id, status="ok", records_in=stats["features"],
                         records_out=stats["kept"], note=as_json(stats))
        return stats
    except Exception as exc:
        store.record_health("ndw", False, 0, error=str(exc)[:500])
        store.alert("source_failure", f"ndw ingest failed: {exc}", source="ndw")
        store.finish_run(run_id, status="failed", error=str(exc)[:2000])
        raise
