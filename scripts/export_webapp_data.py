"""Export the scored private depot sites to the static map (GeoJSON + summary).

Coordinates come from the underlying signals (geocoded via PDOK). A premises row whose
signals have no geometry is geocoded here on the fly — postcode first, then city — and the
result is tagged with its precision so the map can be honest about it.

    .venv/bin/python scripts/export_webapp_data.py
"""
from __future__ import annotations

import json
import pathlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from depotmon.config import Settings
from depotmon.fetch import Fetcher
from depotmon.news_extract import plausible_city
from depotmon.resolve.geocode import build_query, geocode
from depotmon.store import Store

OUT = Path("webapp/public/data")
TIER_ORDER = {"A": 3, "B": 2, "C": 1}



# --- province from coordinates (PDOK provinciegrenzen, downloaded once) --------
_PROVINCES: list | None = None


def _provinces() -> list:
    global _PROVINCES
    if _PROVINCES is None:
        path = pathlib.Path(__file__).resolve().parents[1] / "resources/geo/provinces.geojson"
        try:
            gj = json.loads(path.read_text())
            _PROVINCES = [(f["properties"]["statnaam"], f["geometry"]) for f in gj["features"]]
        except Exception:
            _PROVINCES = []
    return _PROVINCES


def _in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def province_for(lon: float, lat: float) -> str | None:
    """Ray casting against the 12 Dutch province polygons."""
    for name, geom in _provinces():
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            if not poly:
                continue
            if _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]):
                return name
    return None

def evidence_urls(ev) -> list[str]:
    if not isinstance(ev, list):
        return []
    out = []
    for item in ev:
        if isinstance(item, dict) and item.get("url"):
            out.append(str(item["url"]))
        elif isinstance(item, str) and item.startswith("http"):
            out.append(item)
    return out


def main() -> None:
    settings = Settings()
    store = Store(settings.database_url)
    fetcher = Fetcher(settings, store=store, run_id=None, source="export")

    with store.conn.cursor() as cur:
        cur.execute(
            """SELECT place_key, operator, city, postcode, address, tier, score,
                      power_kw, n_points, hgv_capable, channels, evidence,
                      lon, lat, location_basis, access_class
                 FROM premises_scores
                ORDER BY score DESC"""
        )
        rows = list(cur.fetchall())

    features: list[dict] = []
    no_geo: list[str] = []
    precision_counts: Counter[str] = Counter()
    for row in rows:
        # A depot must be drawn at the company's yard when we know it. Only when
        # no company match exists do we fall back to postcode/city, and then the
        # feature says so explicitly (locationBasis='city').
        urls = evidence_urls(row["evidence"])
        lon, lat = row["lon"], row["lat"]
        # Whether this row carries enough TEXT to geocode. It is not a reason to
        # discard it: an OSM charge point has exact geometry and no address at all,
        # and a guard here used to bin 500+ real locations before their signal
        # geometry was ever consulted. Decide with the coordinates, not the text.
        geocodable = bool(row["postcode"] or plausible_city(row["city"]))
        province = None
        country = ""
        precision = "premises" if (lon is not None and lat is not None) else None
        if lon is not None and lat is not None:
            # Snapped to the company's own yard by resolve/snap.py — the only basis
            # the user accepts for a named company ("Oegema, Apeldoorn" must be the
            # yard on Transportcentrum, not the middle of the city).
            with store.conn.cursor() as cur:
                if urls:
                    cur.execute(
                        """SELECT province, raw_state->>'country' AS country
                            FROM signals
                            WHERE doc_url = ANY(%s) AND geom IS NOT NULL
                            ORDER BY last_seen DESC LIMIT 1""",
                        (urls,),
                    )
                    hit = cur.fetchone()
                    if hit:
                        province = hit["province"]
                        # doc_url is shared by the whole Depotcharge fleet, so the
                        # country behind it is arbitrary. Only keep it when the
                        # province proves the feature is Dutch; everything else is
                        # labelled by proximity after the list is built.
                        country = (hit.get("country") or "") if province else ""
        with store.conn.cursor() as cur:
            if lat is None and urls:
                cur.execute(
                    """SELECT lon, lat, province FROM signals
                        WHERE doc_url = ANY(%s) AND geom IS NOT NULL
                        ORDER BY last_seen DESC LIMIT 1""",
                    (urls,),
                )
                hit = cur.fetchone()
                if hit and hit["lat"] is not None:
                    # a signal point is the article's own geocode: address-level only
                    # if the signal carried a postcode/street, never a bare city
                    lon, lat, province = hit["lon"], hit["lat"], hit["province"]
                    precision = "signal"
            # Both fields must be present: ILIKE '%%' on an empty operator or city
            # returns an arbitrary signal from anywhere in the country.
            if lat is None and row["operator"] and row["city"]:
                cur.execute(
                    """SELECT lon, lat, province FROM signals
                        WHERE operator ILIKE %s AND city ILIKE %s AND geom IS NOT NULL
                        ORDER BY last_seen DESC LIMIT 1""",
                    (row["operator"], row["city"]),
                )
                hit = cur.fetchone()
                if hit and hit["lat"] is not None:
                    lon, lat, province = hit["lon"], hit["lat"], hit["province"]
                    precision = "signal"
        if lat is None and geocodable:
            query = build_query(row["address"], row["postcode"], row["city"])
            res = geocode(fetcher, store, query, row["postcode"]) if query else None
            if res:
                lon, lat, precision = res["lon"], res["lat"], res["precision"]
                province = (res["payload"] or {}).get("provincienaam") or province
        if lat is None:
            no_geo.append(row["place_key"])
            continue
        if not province and lon is not None:
            province = province_for(lon, lat)
        precision_counts[precision or "unknown"] += 1
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "id": row["place_key"],
                    "operator": row["operator"] or "Onbekend",
                    "named": not str(row["operator"] or "").startswith("Laadplein "),
                    "address": row["address"] or "",
                    "city": row["city"] or "",
                    "postcode": row["postcode"] or "",
                    "province": province or "",
                    # ISO-2. The Depotcharge feed is multi-country now, so a
                    # feature outside NL must not look like a broken Dutch row.
                    "country": country or ("NL" if province else ""),
                    "tier": row["tier"],
                    "score": round(float(row["score"]), 3),
                    "powerKw": float(row["power_kw"]) if row["power_kw"] else None,
                    "nPoints": int(row["n_points"]) if row["n_points"] else None,
                    "hgv": bool(row["hgv_capable"]),
                    # Is the truck signal a real rating, or only power? A 350 kW
                    # Fastned site is not the same evidence as a pull-through bay.
                    "hgvBasis": (
                        "rated" if row["hgv_capable"]
                        and ({"depotcharge", "osm"} & set(row["channels"] or []))
                        else "power_proxy" if (row["power_kw"] or 0) >= 350
                        else "text_mention" if row["hgv_capable"]
                        else "none"),
                    # Publiek / semi-publiek / privaat — RVO dashboard terminology.
                    "accessClass": row["access_class"] or "unknown",
                    "channels": list(row["channels"] or []),
                    "evidenceCount": len(urls),
                    "evidence": urls[:6],
                    "precision": precision or "unknown",
                    "locationBasis": row["location_basis"] or "city",
                },
            }
        )

    # Label foreign features. Every Depotcharge signal shares one doc_url (the
    # fleet URL), so the per-feature country join returns an arbitrary row. Use
    # the nearest point that declares a country (Depotcharge or eTrucker)
    # instead, and only outside the NL box, so a Dutch row with an unknown
    # province is never relabelled as German.
    with store.conn.cursor() as cur:
        cur.execute(
            """SELECT lon, lat, raw_state->>'country' AS c
                 FROM signals
                WHERE source IN ('depotcharge', 'etrucker')
                  AND raw_state->>'country' IS NOT NULL
                  AND lon IS NOT NULL AND lat IS NOT NULL"""
        )
        geo_points = cur.fetchall()

    if geo_points:
        for feat in features:
            props = feat["properties"]
            if props.get("province"):
                continue
            x, y = feat["geometry"]["coordinates"][:2]
            if 3.2 <= x <= 7.3 and 50.7 <= y <= 53.6:
                continue
            best = min(geo_points, key=lambda r: (r["lon"] - x) ** 2 + (r["lat"] - y) ** 2)
            props["country"] = best["c"] or ""

    # eTrucker declares ISO-3 (DEU, FRA); the UI labels are ISO-2. Dutch rows with
    # no province were skipped by the proximity step above, so they kept an empty
    # country even though they sit inside the NL box.
    ISO3_TO_ISO2 = {
        "NLD": "NL", "DEU": "DE", "BEL": "BE", "AUT": "AT", "CHE": "CH",
        "ESP": "ES", "ITA": "IT", "LUX": "LU", "LIE": "LI", "FRA": "FR",
        "DNK": "DK", "POL": "PL", "CZE": "CZ", "SWE": "SE", "NOR": "NO",
    }
    for feat in features:
        props = feat["properties"]
        code = (props.get("country") or "").strip().upper()
        code = ISO3_TO_ISO2.get(code, code if len(code) == 2 else "")
        if not code:
            x, y = feat["geometry"]["coordinates"][:2]
            if 3.2 <= x <= 7.3 and 50.7 <= y <= 53.6:
                code = "NL"
        props["country"] = code

    features.sort(key=lambda f: (-TIER_ORDER[f["properties"]["tier"]], -f["properties"]["score"]))
    by_tier = Counter(f["properties"]["tier"] for f in features)
    by_province = Counter(f["properties"]["province"] or "Onbekend" for f in features)
    by_channel: Counter[str] = Counter()
    for f in features:
        for ch in f["properties"]["channels"]:
            by_channel[ch] += 1
    by_access = Counter(f["properties"]["accessClass"] for f in features)
    total_kw = sum(f["properties"]["powerKw"] or 0 for f in features)
    known_kw = sum(1 for f in features if f["properties"]["powerKw"])

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "depots.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )
    summary = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sites": {
            "total": len(features),
            "scored": len(rows),
            "withoutCoords": len(no_geo),
            "byTier": {t: by_tier.get(t, 0) for t in ("A", "B", "C")},
            "hgv": sum(1 for f in features if f["properties"]["hgv"]),
            "byAccess": {k: by_access.get(k, 0)
                         for k in ("public", "semi_public", "private", "unknown")},
        },
        "power": {"totalKw": round(total_kw, 1), "sitesWithKw": known_kw},
        "byProvince": dict(by_province.most_common()),
        "byChannel": dict(by_channel.most_common()),
        "precision": dict(precision_counts),
        "sources": {
            "permits": "KOOP SRU 2.0 — Officiële Bekendmakingen (gemeente-/provinciaal blad, Staatscourant)",
            "news": "publisher RSS + sitemap-archief (TTM, Nieuwsblad Transport, Tankpro, WattisDuurzaam, Change.inc, Schuttevaer)",
            "geocoding": "PDOK Locatieserver",
            "counterEvidence": ("NDW laadpuntenregister — tegenbewijs voor een privaat depot op "
                                "dezelfde locatie; HDV-capabele punten (>= 350 kW of truckrating) "
                                "tellen als publieke laadlocatie"),
            "accessTaxonomy": ("Publiek / semi-publiek / privaat volgens RVO Dashboard "
                               "Laadinfrastructuur (dashboardklimaatbeleid.nl)"),
        },
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["sites"]), "->", OUT)
    if no_geo:
        print("geen coördinaten:", len(no_geo), no_geo[:8])


if __name__ == "__main__":
    main()
