"""Export the scored private depot sites to the static map (GeoJSON + summary).

Coordinates come from the underlying signals (geocoded via PDOK). A premises row whose
signals have no geometry is geocoded here on the fly — postcode first, then city — and the
result is tagged with its precision so the map can be honest about it.

    .venv/bin/python scripts/export_webapp_data.py
"""
from __future__ import annotations

import json
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
                      lon, lat, location_basis
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
        pass
        # A row with neither a postcode nor a plausible Dutch place name cannot be
        # placed on a map of the Netherlands ("Düsseldorf", "").
        if not (row["postcode"] or plausible_city(row["city"])):
            no_geo.append(row["place_key"])
            continue
        urls = evidence_urls(row["evidence"])
        lon, lat = row["lon"], row["lat"]
        province = None
        precision = "premises" if (lon is not None and lat is not None) else None
        if lon is not None and lat is not None:
            # Snapped to the company's own yard by resolve/snap.py — the only basis
            # the user accepts for a named company ("Oegema, Apeldoorn" must be the
            # yard on Transportcentrum, not the middle of the city).
            with store.conn.cursor() as cur:
                if urls:
                    cur.execute(
                        """SELECT province FROM signals
                            WHERE doc_url = ANY(%s) AND geom IS NOT NULL
                            ORDER BY last_seen DESC LIMIT 1""",
                        (urls,),
                    )
                    hit = cur.fetchone()
                    if hit:
                        province = hit["province"]
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
            if lat is None:
                cur.execute(
                    """SELECT lon, lat, province FROM signals
                        WHERE operator ILIKE %s AND city ILIKE %s AND geom IS NOT NULL
                        ORDER BY last_seen DESC LIMIT 1""",
                    (row["operator"] or "", row["city"] or ""),
                )
                hit = cur.fetchone()
                if hit and hit["lat"] is not None:
                    lon, lat, province = hit["lon"], hit["lat"], hit["province"]
                    precision = "signal"
        if lat is None:
            query = build_query(row["address"], row["postcode"], row["city"])
            res = geocode(fetcher, store, query, row["postcode"]) if query else None
            if res:
                lon, lat, precision = res["lon"], res["lat"], res["precision"]
                province = (res["payload"] or {}).get("provincienaam") or province
        if lat is None:
            no_geo.append(row["place_key"])
            continue
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
                    "tier": row["tier"],
                    "score": round(float(row["score"]), 3),
                    "powerKw": float(row["power_kw"]) if row["power_kw"] else None,
                    "nPoints": int(row["n_points"]) if row["n_points"] else None,
                    "hgv": bool(row["hgv_capable"]),
                    "channels": list(row["channels"] or []),
                    "evidenceCount": len(urls),
                    "evidence": urls[:6],
                    "precision": precision or "unknown",
                    "locationBasis": row["location_basis"] or "city",
                },
            }
        )

    features.sort(key=lambda f: (-TIER_ORDER[f["properties"]["tier"]], -f["properties"]["score"]))
    by_tier = Counter(f["properties"]["tier"] for f in features)
    by_province = Counter(f["properties"]["province"] or "Onbekend" for f in features)
    by_channel: Counter[str] = Counter()
    for f in features:
        for ch in f["properties"]["channels"]:
            by_channel[ch] += 1
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
        },
        "power": {"totalKw": round(total_kw, 1), "sitesWithKw": known_kw},
        "byProvince": dict(by_province.most_common()),
        "byChannel": dict(by_channel.most_common()),
        "precision": dict(precision_counts),
        "sources": {
            "permits": "KOOP SRU 2.0 — Officiële Bekendmakingen (gemeente-/provinciaal blad, Staatscourant)",
            "news": "publisher RSS + sitemap-archief (TTM, Nieuwsblad Transport, Tankpro, WattisDuurzaam, Change.inc, Schuttevaer)",
            "geocoding": "PDOK Locatieserver",
            "counterEvidence": "NDW/OCM laadpuntenregister (alleen als tegenbewijs: publiek laadplein)",
        },
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["sites"]), "->", OUT)
    if no_geo:
        print("geen coördinaten:", len(no_geo), no_geo[:8])


if __name__ == "__main__":
    main()
