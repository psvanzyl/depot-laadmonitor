"""Resolve: cluster signals into one row per physical depot site.

Clustering is geographic (PostGIS ST_ClusterDBSCAN, 80 m) over *depot-relevant* signals
only: permits, OSM charge points tagged for trucks or non-public access, and NDW locations
of ≥ 350 kW. Everything else (ordinary public chargers) is never promoted to a site; it can
only be attached as operational evidence to an existing cluster within 120 m.
"""

from __future__ import annotations

import hashlib
import json
from difflib import SequenceMatcher
from uuid import uuid4

from ..config import Settings
from ..store import Store

EPS_METERS = 80
ATTACH_METERS = 120
OPERATOR_SIMILARITY = 0.85

CORE_SQL = """
SELECT signal_id, kind, source, source_key, name, operator, address, postcode, city,
       bag_id, power_kw, n_points, connector_types, hgv_capable, access, status_hint,
       text_excerpt, doc_url, observed_at, province, municipality, lon, lat, geom
FROM signals
WHERE geom IS NOT NULL
  AND last_seen >= now() - interval '400 days'
  AND (
        kind = 'permit'
     OR (kind = 'news' AND (postcode IS NOT NULL OR address IS NOT NULL))
     OR (kind = 'osm' AND (hgv_capable OR access IN ('private','customers')))
     OR (kind = 'ndw' AND power_kw >= 350)
  )
"""


def _sim(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _first(values):
    for v in values:
        if v:
            return v
    return None


def _max(values):
    clean = [float(v) for v in values if v is not None]
    return max(clean) if clean else None


def _merge_cluster(members: list[dict], attached: list[dict]) -> dict:
    permits = [m for m in members if m["kind"] == "permit"]
    osm = [m for m in members if m["kind"] == "osm"]
    chargers = [m for m in members + attached if m["kind"] in {"ndw", "osm"}]

    connectors = sorted({c for m in members + attached for c in (m["connector_types"] or [])})
    operator = _first([m["operator"] for m in permits]) or _first(
        [m["operator"] for m in osm]
    ) or _first([m["operator"] for m in attached])
    address = _first([m["address"] for m in osm if m["address"]]) or _first(
        [m["address"] for m in members if m["address"]]
    )
    postcode = _first([m["postcode"] for m in members + attached if m["postcode"]])
    city = _first([m["city"] for m in members + attached if m["city"]])
    province = _first([m["province"] for m in members + attached if m["province"]])
    municipality = _first([m["municipality"] for m in members + attached if m["municipality"]])
    bag_id = _first([m["bag_id"] for m in members + attached if m["bag_id"]])

    announced = _max([m["power_kw"] for m in permits])
    operational = _max([m["power_kw"] for m in chargers])
    n_points = sum(int(m["n_points"]) for m in chargers if m["n_points"]) or None

    access = _first([m["access"] for m in osm if m["access"] and m["access"] != "unknown"])
    if access is None:
        access = _first([m["access"] for m in attached if m["access"] not in (None, "unknown")])

    name = _first([m["name"] for m in permits])
    if not name or len(name) < 4:
        base = operator or "Laadlocatie"
        name = f"{base} ({city})" if city else base

    return {
        "name": name[:200],
        "operator": operator,
        "address": address,
        "postcode": postcode,
        "city": city,
        "province": province,
        "municipality": municipality,
        "bag_id": bag_id,
        "power_kw_announced": announced,
        "power_kw_operational": operational,
        "n_points": n_points,
        "connector_types": connectors or None,
        "hgv_capable": any(bool(m["hgv_capable"]) for m in members + attached),
        "access": access,
        "member_ids": [m["signal_id"] for m in members],
        "attached_ids": [m["signal_id"] for m in attached],
    }


def _cluster_merge_key(merge: dict) -> str:
    return "|".join(
        [
            (merge.get("postcode") or "").upper(),
            (merge.get("operator") or "").lower().strip(),
        ]
    )


def build_sites(store: Store, settings: Settings) -> dict:
    """Cluster signals and upsert sites. Returns statistics."""
    run_id = store.start_run("resolve", "sites")
    stats = {"core_signals": 0, "clusters": 0, "sites_new": 0, "sites_updated": 0,
             "merged_by_operator": 0, "attached": 0}
    try:
        with store.conn.cursor() as cur:
            cur.execute(CORE_SQL)
            core = list(cur.fetchall())
        stats["core_signals"] = len(core)
        if not core:
            store.finish_run(run_id, status="ok", note=json.dumps(stats))
            return stats

        ids = [r["signal_id"] for r in core]
        with store.conn.cursor() as cur:
            cur.execute(
                """WITH core AS (SELECT geom, signal_id FROM signals WHERE signal_id = ANY(%s)),
                        clustered AS (
                          SELECT signal_id, ST_ClusterDBSCAN(geom, %s, 1) OVER () AS cid
                          FROM core)
                   SELECT cid, array_agg(signal_id) AS ids, COUNT(*) AS n,
                          ST_AsText(ST_Centroid(ST_Collect(s.geom))) AS centroid
                   FROM clustered c JOIN signals s USING (signal_id)
                   GROUP BY cid ORDER BY cid""",
                (ids, EPS_METERS),
            )
            groups = list(cur.fetchall())

        by_id = {r["signal_id"]: r for r in core}
        clusters = []
        for g in groups:
            members = [by_id[i] for i in g["ids"]]
            clusters.append({"ids": g["ids"], "members": members, "n": g["n"]})

        # second-pass merge: same postcode + near-identical operator name
        merged: list[dict] = []
        used = [False] * len(clusters)
        for i, cl in enumerate(clusters):
            if used[i]:
                continue
            acc = cl
            key_i = _cluster_merge_key(_merge_cluster(cl["members"], []))
            for j in range(i + 1, len(clusters)):
                if used[j]:
                    continue
                other = clusters[j]
                key_j = _cluster_merge_key(_merge_cluster(other["members"], []))
                if not key_i.strip("|") or not key_j.strip("|"):
                    continue
                pc_i, op_i = key_i.split("|")
                pc_j, op_j = key_j.split("|")
                if pc_i and pc_i == pc_j and _sim(op_i, op_j) >= OPERATOR_SIMILARITY:
                    acc = {"ids": acc["ids"] + other["ids"],
                           "members": acc["members"] + other["members"],
                           "n": acc["n"] + other["n"]}
                    used[j] = True
                    stats["merged_by_operator"] += 1
            used[i] = True
            merged.append(acc)

        stats["clusters"] = len(merged)

        with store.conn.cursor() as cur:
            cur.execute("SELECT site_id, geom, bag_id, state_hash FROM sites")
            existing = list(cur.fetchall())

        for cl in merged:
            member_ids = cl["ids"]
            centroid = None
            with store.conn.cursor() as cur:
                cur.execute(
                    """SELECT ST_X(ST_Centroid(ST_Collect(geom))) AS x,
                              ST_Y(ST_Centroid(ST_Collect(geom))) AS y
                       FROM signals WHERE signal_id = ANY(%s)""",
                    (member_ids,),
                )
                c = cur.fetchone()
                centroid = (c["x"], c["y"])
                cur.execute(
                    """SELECT * FROM signals
                       WHERE kind IN ('ndw','osm') AND signal_id <> ALL(%s)
                         AND last_seen >= now() - interval '400 days'
                         AND ST_DWithin(geom,
                              ST_SetSRID(ST_MakePoint(%s,%s),28992), %s)""",
                    (member_ids, centroid[0], centroid[1], ATTACH_METERS),
                )
                attached = list(cur.fetchall())
            stats["attached"] += len(attached)

            merge = _merge_cluster(cl["members"], attached)
            site_id, is_new = _match_or_create(store, existing, merge, centroid)
            _upsert_site(store, site_id, merge, centroid)
            _write_evidence(store, site_id, cl["members"], attached)

            with store.tx() as cur:
                cur.execute(
                    "UPDATE signals SET matched_site_id=%s WHERE signal_id = ANY(%s)",
                    (site_id, member_ids + [a["signal_id"] for a in attached]),
                )
            stats["sites_new" if is_new else "sites_updated"] += 1

        store.finish_run(run_id, status="ok", records_in=stats["core_signals"],
                         records_out=stats["clusters"], note=json.dumps(stats))
        return stats
    except Exception as exc:
        store.finish_run(run_id, status="failed", error=str(exc)[:2000])
        raise


def _match_or_create(store: Store, existing: list[dict], merge: dict,
                     centroid: tuple[float, float]) -> tuple[str, bool]:
    """Reuse the nearest existing site (≤100 m or same BAG id) so ids are stable."""
    with store.conn.cursor() as cur:
        cur.execute(
            """SELECT site_id FROM sites
               WHERE ST_DWithin(geom, ST_SetSRID(ST_MakePoint(%s,%s),28992), 100)
                  OR (%s::text IS NOT NULL AND bag_id = %s::text)
               ORDER BY ST_Distance(geom, ST_SetSRID(ST_MakePoint(%s,%s),28992))
               LIMIT 1""",
            (centroid[0], centroid[1], merge.get("bag_id"), merge.get("bag_id"),
             centroid[0], centroid[1]),
        )
        row = cur.fetchone()
    if row:
        return str(row["site_id"]), False
    site_id = str(uuid4())
    existing.append({"site_id": site_id, "geom": None,
                     "bag_id": merge.get("bag_id"), "state_hash": None})
    return site_id, True


def _upsert_site(store: Store, site_id: str, merge: dict,
                 centroid: tuple[float, float]) -> None:
    with store.tx() as cur:
        cur.execute(
            """INSERT INTO sites(site_id, name, operator, address, postcode, city, bag_id,
                                 geom, power_kw_announced, power_kw_operational, n_points,
                                 connector_types, hgv_capable, access, province, municipality)
               VALUES (%s,%s,%s,%s,%s,%s,%s,
                       ST_SetSRID(ST_MakePoint(%s,%s),28992),
                       %s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (site_id) DO UPDATE SET
                 name=EXCLUDED.name,
                 operator=COALESCE(EXCLUDED.operator, sites.operator),
                 address=COALESCE(EXCLUDED.address, sites.address),
                 postcode=COALESCE(EXCLUDED.postcode, sites.postcode),
                 city=COALESCE(EXCLUDED.city, sites.city),
                 bag_id=COALESCE(EXCLUDED.bag_id, sites.bag_id),
                 geom=EXCLUDED.geom,
                 power_kw_announced=GREATEST(COALESCE(EXCLUDED.power_kw_announced,0),
                                             COALESCE(sites.power_kw_announced,0)),
                 power_kw_operational=GREATEST(COALESCE(EXCLUDED.power_kw_operational,0),
                                               COALESCE(sites.power_kw_operational,0)),
                 n_points=COALESCE(EXCLUDED.n_points, sites.n_points),
                 connector_types=COALESCE(EXCLUDED.connector_types, sites.connector_types),
                 hgv_capable=EXCLUDED.hgv_capable OR sites.hgv_capable,
                 access=COALESCE(EXCLUDED.access, sites.access),
                 province=COALESCE(EXCLUDED.province, sites.province),
                 municipality=COALESCE(EXCLUDED.municipality, sites.municipality),
                 last_seen=now(), updated_at=now()""",
            (
                site_id, merge["name"], merge["operator"], merge["address"],
                merge["postcode"], merge["city"], merge["bag_id"],
                centroid[0], centroid[1],
                merge["power_kw_announced"], merge["power_kw_operational"], merge["n_points"],
                merge["connector_types"], merge["hgv_capable"], merge["access"],
                merge["province"], merge["municipality"],
            ),
        )


SOURCE_CONFIDENCE = {"permit": 0.75, "osm": 0.6, "ndw": 0.55}


def _write_evidence(store: Store, site_id: str, members: list[dict],
                    attached: list[dict]) -> None:
    with store.tx() as cur:
        cur.execute("DELETE FROM evidence WHERE site_id=%s", (site_id,))
        for m in members + attached:
            excerpt = m.get("text_excerpt")
            if not excerpt and m["kind"] in {"ndw", "osm"}:
                excerpt = f"{m['name']} — {m['power_kw'] or '?'} kW"
            cur.execute(
                """INSERT INTO evidence(site_id, source, url, observed_at, fetched_at,
                                        excerpt, confidence)
                   VALUES (%s,%s,%s,%s,now(),%s,%s)""",
                (
                    site_id,
                    m["source"],
                    m.get("doc_url"),
                    m.get("observed_at"),
                    (excerpt or "")[:1000],
                    SOURCE_CONFIDENCE.get(m["kind"], 0.4),
                ),
            )
