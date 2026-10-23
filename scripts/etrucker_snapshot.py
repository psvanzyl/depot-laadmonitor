#!/usr/bin/env python3
"""Capture an eTrucker snapshot: HDV-relevant charging locations.

eTrucker's app signs in anonymously with its own public Firebase client key, then
reads the `location` collection. That collection is readable by such a user, so
this capture uses the same mechanism the app uses at first launch: anonymous
sign-in, read only, no account of the boss's is involved.

The raw payload is large (one document per European site, ~16 KB each), so each
document is reduced here to the handful of fields the map needs and the truck
gate is applied immediately. Only HDV-relevant records reach the snapshot.

Gate (explicit truck evidence, never a name guess):
  heavy_vehicles_dedicated / _drive_through / _rigid_vehicles /
  _trailer_accessibility / _dangerous_goods / _overnight_parking
  or a restriction code N2/N3, or an MCS connector.

Key location, never in the repo:
  env ETRUCKER_FIREBASE_KEY, or /root/.etrucker/firebase_key (mode 0600)

Usage:
  scripts/etrucker_snapshot.py                 # default country list
  scripts/etrucker_snapshot.py NLD,DEU,BEL     # explicit list
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

PROJECT = "etrucker-prod"
BASE = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents"
OUT = pathlib.Path(os.environ.get("ETRUCKER_DIR", "data/etrucker"))
DEFAULT_COUNTRIES = ("NLD,DEU,BEL,LUX,AUT,CHE,ESP,PRT,FRA,ITA,DNK,SWE,NOR,FIN,"
                     "POL,CZE,SVK,HUN,SVN,HRV,IRL,GBR,EST,LVA,LTU,ROU,BGR,GRC")

TRUCK_FLAGS = (
    "heavy_vehicles_dedicated",
    "heavy_vehicles_drive_through",
    "heavy_vehicles_rigid_vehicles",
    "heavy_vehicles_trailer_accessibility",
    "heavy_vehicles_dangerous_goods",
    "heavy_vehicles_overnight_parking",
)
HGV_CODES = {"N2", "N3", "N2_N3"}

CONNECTOR_MAP = {
    "IEC_62196_T2_COMBO": "CCS",
    "IEC_62196_T2": "TYPE2",
    "CHADEMO": "CHAdeMO",
    "MCS": "MCS",
    "TESLA_S": "TESLA",
    "DOMESTIC_A": "SCHUKO",
}


def api_key() -> str:
    key = os.environ.get("ETRUCKER_FIREBASE_KEY", "").strip()
    if key:
        return key
    path = pathlib.Path("/root/.etrucker/firebase_key")
    if path.exists():
        return path.read_text().strip()
    sys.exit("ERROR: no eTrucker client key (env ETRUCKER_FIREBASE_KEY or /root/.etrucker/firebase_key)")


def signup(key: str) -> str:
    req = urllib.request.Request(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={key}",
        data=json.dumps({"returnSecureToken": True}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as fh:
        return json.loads(fh.read().decode())["idToken"]


def run_query(token: str, country: str, cursor: str | None) -> list[dict]:
    query: dict = {
        "structuredQuery": {
            "from": [{"collectionId": "location"}],
            "where": {"fieldFilter": {
                "field": {"fieldPath": "country"}, "op": "EQUAL",
                "value": {"stringValue": country}}},
            "orderBy": [{"field": {"fieldPath": "__name__"}, "direction": "ASCENDING"}],
            "limit": 300,
        }
    }
    if cursor:
        query["structuredQuery"]["startAt"] = {
            "values": [{"referenceValue": cursor}], "before": False}
    req = urllib.request.Request(
        f"{BASE}:runQuery", data=json.dumps(query).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as fh:
        payload = json.loads(fh.read().decode())
    return [r["document"] for r in payload if "document" in r]


def val(fields: dict, key: str):
    """Unwrap one Firestore REST value into plain python."""
    v = fields.get(key)
    if not isinstance(v, dict) or not v:
        return None
    kind = next(iter(v))
    raw = v[kind]
    if kind == "mapValue":
        return raw.get("fields") or {}
    if kind == "arrayValue":
        return raw.get("values") or []
    return raw


def scalar(v):
    if isinstance(v, dict):
        return next(iter(v.values()), None)
    return v


def text(fields: dict, key: str) -> str | None:
    v = val(fields, key)
    if isinstance(v, dict):
        v = val(v, "name") or val(v, "description") or v
    v = scalar(v)
    return str(v).strip() if v not in (None, "") else None


def fnum(v) -> float:
    try:
        return float(scalar(v))
    except (TypeError, ValueError):
        return 0.0


def to_record(doc: dict) -> dict | None:
    f = doc.get("fields") or {}
    if str(scalar(val(f, "deleted"))).lower() == "true":
        return None

    coords = val(f, "coordinates") or {}
    lat, lon = fnum(coords.get("latitude")), fnum(coords.get("longitude"))
    if not lat or not lon:
        return None

    hv = val(f, "heavy_vehicles") or {}
    flags = {flag: bool(fnum(hv.get(flag)) or scalar(hv.get(flag)) is True)
             for flag in TRUCK_FLAGS}
    restrictions: list[str] = []
    codes = val(hv, "heavy_vehicles_restrictions") or []
    for c in codes:
        s = scalar(c)
        if s:
            restrictions.append(str(s))

    powers: list[float] = []
    standards: set[str] = set()
    points = 0
    for evse in (val(f, "evses") or []):
        ef = (evse.get("mapValue") or {}).get("fields", {})
        for conn in (val(ef, "connectors") or []):
            cf = (conn.get("mapValue") or {}).get("fields", {})
            watts = fnum(val(cf, "max_power"))
            if watts > 0:
                powers.append(watts / 1000.0)
            std = scalar(val(cf, "standard")) or "UNKNOWN"
            std = str(std)
            standards.add("MCS" if "MCS" in std else CONNECTOR_MAP.get(std, std))
            points += 1

    hgv = (any(flags.values()) or bool(set(restrictions) & HGV_CODES)
           or "MCS" in standards)
    if not hgv:
        return None

    loc_type = scalar(val(f, "location_type")) or "ON_STREET"
    if isinstance(loc_type, dict):
        loc_type = None
    access = "private" if str(text(f, "type") or "").upper() == "PRIVATE" else "public"

    return {
        "id": text(f, "id") or doc.get("name", "").rsplit("/", 1)[-1],
        "name": text(f, "name"),
        "operator": text(f, "operator") or text(f, "brand_name") or text(f, "owner"),
        "address": text(f, "address"),
        "postcode": (text(f, "postal_code") or "").replace(" ", "") or None,
        "city": text(f, "city"),
        "country": text(f, "country"),
        "lat": lat,
        "lon": lon,
        "max_kw": round(max(powers), 1) if powers else None,
        "n_points": points or None,
        "connectors": sorted(standards) or None,
        "access": access,
        "location_type": str(loc_type) if loc_type else None,
        "hgv_flags": {k: v for k, v in flags.items() if v},
        "hgv_restrictions": restrictions,
        "last_updated": text(f, "last_updated"),
    }


def main() -> None:
    countries = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_COUNTRIES).split(",")
    countries = [c.strip().upper() for c in countries if c.strip()]
    OUT.mkdir(parents=True, exist_ok=True)
    token = signup(api_key())

    records: dict[str, dict] = {}
    for cc in countries:
        page, cursor, raw, kept = 0, None, 0, 0
        while True:
            docs = run_query(token, cc, cursor)
            page += 1
            raw += len(docs)
            for doc in docs:
                rec = to_record(doc)
                if rec:
                    records[rec["id"]] = rec
                    kept += 1
            if len(docs) < 300:
                break
            cursor = docs[-1]["name"]
            time.sleep(0.25)
        print(f"{cc}: {raw} locations read, {kept} HDV-kept (pages {page})", flush=True)

    rows = sorted(records.values(), key=lambda r: (r["country"] or "", r["id"]))
    path = OUT / "locations.json"
    path.write_text(json.dumps(rows, indent=None))
    print(f"\nwrote {len(rows)} HDV locations -> {path} ({path.stat().st_size // 1024} KB)")
    by_country: dict[str, int] = {}
    for r in rows:
        by_country[r["country"] or "?"] = by_country.get(r["country"] or "?", 0) + 1
    print("by country:", dict(sorted(by_country.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
