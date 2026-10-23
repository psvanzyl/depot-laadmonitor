"""Attach each depot premises to the company's own yard.

The failure this fixes: a news article says "Oegema, Apeldoorn", the geocoder returns
the centre of Apeldoorn, and a 200-truck depot is rendered in a shopping street. The
company base layer knows where Oegema actually is, so match on the company name and
move the marker to that premise. If no company matches, the row keeps no coordinates
here and the exporter is required to label it as a city approximation.
"""
from __future__ import annotations

from collections import defaultdict

from ..news_extract import normalize_company
from ..store import Store


def _index(rows):
    by_norm = defaultdict(list)
    for r in rows:
        by_norm[r["name_norm"]].append(r)
    return by_norm


def snap(store: Store) -> dict:
    with store.tx() as cur:
        cur.execute(
            """SELECT company_id, name, name_norm, city, kind,
                      ST_Y(geom) AS lat, ST_X(geom) AS lon
                 FROM companies WHERE geom IS NOT NULL"""
        )
        companies = cur.fetchall()
        idx = _index(companies)

        cur.execute("SELECT company_key, place_key, operator, city, location_basis"
                    " FROM premises_scores")
        premises = cur.fetchall()

    stats = {"premises": len(premises), "companies": len(companies), "snapped": 0,
             "city_only": 0}
    updates = []
    for p in premises:
        if (p["location_basis"] or "") == "signal":
            # The evidence carried exact coordinates; never move a registry point
            # to a company address.
            stats["signal_placed"] = stats.get("signal_placed", 0) + 1
            continue
        op = (p["operator"] or "").strip()
        city = (p["city"] or "").strip()
        if not op or op.startswith("Laadplein "):
            stats["city_only"] += 1
            continue
        norm = normalize_company(op)
        cands = idx.get(norm) or []
        if not cands and len(norm) >= 6:
            # "Oegema Transport" vs "Oegema Transport BV": prefix match, same town first
            for key, vals in idx.items():
                if key.startswith(norm) or norm.startswith(key):
                    cands.extend(vals)
        if not cands:
            stats["city_only"] += 1
            continue
        if city:
            same = [c for c in cands if (c["city"] or "").strip().lower() == city.lower()]
            cands = same or cands
        best = cands[0]
        updates.append((best["lon"], best["lat"], "premises", best["company_id"],
                        p["company_key"], p["place_key"]))
        stats["snapped"] += 1

    with store.tx() as cur:
        for lon, lat, basis, company_id, company_key, place_key in updates:
            cur.execute(
                """UPDATE premises_scores
                      SET lon = %s, lat = %s, location_basis = %s, company_id = %s
                    WHERE company_key = %s AND place_key = %s""",
                (lon, lat, basis, company_id, company_key, place_key),
            )
    return stats
