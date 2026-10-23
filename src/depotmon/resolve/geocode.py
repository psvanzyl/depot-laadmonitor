"""Resolve: geocode addresses via PDOK Locatieserver, enrich with BAG identifiers."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlencode

from ..config import Settings
from ..fetch import Fetcher
from ..store import Store

LOCATIESERVER = "https://api.pdok.nl/bzk/locatieserver/search/v3_1/free"
BAG_PAND = "https://api.pdok.nl/kadaster/bag/ogc/v2/collections/pand/items"
FIELDS = (
    "id,type,weergavenaam,straatnaam,huisnummer,huisletter,huisnummertoevoeging,"
    "postcode,woonplaatsnaam,gemeentenaam,provincienaam,centroide_ll,"
    "adresseerbaarobject_id,numaanduiding_id,gekoppeld_perceel"
)


def _norm(query: str | None) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def _point(centroide: str | None) -> tuple[float, float] | None:
    if not centroide:
        return None
    m = re.search(r"POINT\(([-\d.]+)\s+([-\d.]+)\)", centroide)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def geocode(fetcher: Fetcher, store: Store, query: str,
            postcode: str | None = None) -> dict | None:
    """Return {lon, lat, precision, payload} or None. Results are cached in geo_cache."""
    q = _norm(query)
    if not q:
        return None  # city-only or empty signals must not abort the batch
    key = hashlib.sha256(q.encode()).hexdigest()
    with store.conn.cursor() as cur:
        cur.execute("SELECT * FROM geo_cache WHERE input_hash=%s", (key,))
        cached = cur.fetchone()
    if cached is not None:
        if not cached["ok"]:
            return None
        return {
            "lon": cached["lon"],
            "lat": cached["lat"],
            "precision": cached["precision"],
            "payload": cached["payload"],
        }

    attempts: list[tuple[str, dict, str]] = [(q, {"fq": "type:adres"}, "address")]
    if postcode:
        attempts.append((postcode, {"fq": "type:postcode"}, "postcode"))
    attempts.append((q, {}, "place"))

    result = None
    for text, extra, precision in attempts:
        params = {"q": text, "rows": 1, "fl": FIELDS, **extra}
        try:
            resp = fetcher.get(LOCATIESERVER, params=params)
        except Exception:
            continue
        if resp.status != 200:
            continue
        docs = (resp.json().get("response") or {}).get("docs") or []
        if not docs:
            continue
        doc = docs[0]
        pt = _point(doc.get("centroide_ll"))
        if pt is None:
            continue
        result = {"lon": pt[0], "lat": pt[1], "precision": precision, "payload": doc}
        break

    payload = json.dumps(result["payload"], ensure_ascii=False) if result else "{}"
    with store.tx() as cur:
        cur.execute(
            """INSERT INTO geo_cache(input_hash, query, ok, lon, lat, precision, payload)
               VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT (input_hash) DO UPDATE SET fetched_at=now()""",
            (
                key,
                query,
                result is not None,
                result["lon"] if result else None,
                result["lat"] if result else None,
                result["precision"] if result else None,
                payload,
            ),
        )
    return result


def bag_pand_id(fetcher: Fetcher, lon: float, lat: float) -> str | None:
    """Nearest BAG pand within ~40 m of the point, via the PDOK OGC API."""
    d = 0.0004  # ≈ 40 m
    try:
        resp = fetcher.get(
            BAG_PAND,
            params={
                "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}",
                "limit": 1,
                "properties": "identificatie",
            },
        )
    except Exception:
        return None
    if resp.status != 200:
        return None
    feats = resp.json().get("features") or []
    if not feats:
        return None
    return str(feats[0].get("properties", {}).get("identificatie") or "") or None


def build_query(address: str | None, postcode: str | None, city: str | None) -> str | None:
    if address and postcode:
        return f"{address}, {postcode}"
    if address and city:
        return f"{address}, {city}"
    if address:
        return address
    if postcode:
        return postcode
    # City-only signals still get a map point (PDOK resolves the place centroid).
    if city:
        return city
    return None


def enrich_signals(store: Store, settings: Settings, *, limit: int = 400) -> dict:
    """Geocode signals without geometry; fill province/municipality for all."""
    run_id = store.start_run("resolve", "geocode")
    stats = {"candidates": 0, "geocoded": 0, "failed": 0, "bag_linked": 0,
             "geo_filled": 0}
    try:
        with store.conn.cursor() as cur:
            cur.execute(
                """SELECT signal_id, address, postcode, city, lon, lat
                   FROM signals
                   WHERE geom IS NULL AND (address IS NOT NULL OR postcode IS NOT NULL)
                   ORDER BY last_seen DESC LIMIT %s""",
                (limit,),
            )
            todo = list(cur.fetchall())
        stats["candidates"] = len(todo)
        with Fetcher(settings, store=store, run_id=run_id, source="geocode") as fetcher:
            for row in todo:
                query = build_query(row["address"], row["postcode"], row["city"])
                if not query:
                    stats["failed"] += 1
                    continue
                try:
                    res = geocode(fetcher, store, query, row["postcode"])
                except Exception:  # noqa: BLE001 — one bad address must not stop the batch
                    res = None
                if res is None:
                    stats["failed"] += 1
                    continue
                bag_id = bag_pand_id(fetcher, res["lon"], res["lat"]) if res["precision"] == "address" else None
                payload = res["payload"]
                with store.tx() as cur:
                    cur.execute(
                        """UPDATE signals SET lon=%s, lat=%s,
                             geom=ST_Transform(ST_SetSRID(ST_MakePoint(%s,%s),4326),28992),
                             bag_id=COALESCE(bag_id,%s),
                             province=COALESCE(province,%s),
                             municipality=COALESCE(municipality,%s)
                           WHERE signal_id=%s""",
                        (
                            res["lon"], res["lat"], res["lon"], res["lat"], bag_id,
                            payload.get("provincienaam"), payload.get("gemeentenaam"),
                            row["signal_id"],
                        ),
                    )
                stats["geocoded"] += 1
                if bag_id:
                    stats["bag_linked"] += 1
                if res["precision"] != "address":
                    stats["geo_filled"] += 1
        store.finish_run(run_id, status="ok", records_in=stats["candidates"],
                         records_out=stats["geocoded"], note=json.dumps(stats))
        return stats
    except Exception as exc:
        store.finish_run(run_id, status="failed", error=str(exc)[:2000])
        raise
