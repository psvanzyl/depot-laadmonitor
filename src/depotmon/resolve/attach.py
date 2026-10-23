"""Match located evidence to the company at that premises."""
from __future__ import annotations

from ..store import Store


def attach(store: Store, radius_m: int = 300) -> dict:
    stats = {"candidates": 0, "attached": 0, "ambiguous": 0}
    with store.tx() as cur:
        cur.execute(
            """SELECT s.signal_id, ST_Y(s.geom) AS lat, ST_X(s.geom) AS lon, s.city
                 FROM signals s
                WHERE s.geom IS NOT NULL
                  AND (s.operator IS NULL OR s.operator = '')
                  AND s.kind IN ('permit', 'news')"""
        )
        rows = cur.fetchall()
        stats["candidates"] = len(rows)
        for r in rows:
            # Strong rule: same 4-digit postcode AND same town. Permits publish both,
            # and a postcode area is small enough that this is the yard, not a neighbour.
            cur.execute(
                """SELECT s.postcode, s.city FROM signals s WHERE s.signal_id = %s""",
                (r["signal_id"],),
            )
            own = cur.fetchone()
            pc4 = (own["postcode"] or "").replace(" ", "")[:4] if own else ""
            hits = []
            if pc4 and own["city"]:
                cur.execute(
                    """SELECT company_id, name, city, address, postcode, kind, 0 AS d
                         FROM companies
                        WHERE geom IS NOT NULL
                          AND left(replace(coalesce(postcode,''),' ',''),4) = %s
                          AND lower(coalesce(city,'')) = lower(%s)
                        LIMIT 2""",
                    (pc4, own["city"]),
                )
                hits = cur.fetchall()
            if not hits:
                cur.execute(
                    """SELECT company_id, name, city, address, postcode, kind,
                              ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography) AS d
                         FROM companies
                        WHERE geom IS NOT NULL
                          AND ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography, %s)
                        ORDER BY d ASC
                        LIMIT 2""",
                    (r["lon"], r["lat"], r["lon"], r["lat"], radius_m),
                )
                hits = cur.fetchall()
            if not hits:
                continue
            # Two different companies inside the radius means we cannot say who the
            # permit is for; leave the row unnamed rather than guessing a neighbour.
            if len(hits) == 2 and hits[1]["d"] < 40:
                stats["ambiguous"] += 1
                continue
            best = hits[0]
            cur.execute(
                """UPDATE signals
                      SET operator = %s, name = COALESCE(NULLIF(name, ''), %s),
                          address = COALESCE(NULLIF(address, ''), %s),
                          postcode = COALESCE(NULLIF(postcode, ''), %s),
                          city = COALESCE(NULLIF(city, ''), %s)
                    WHERE signal_id = %s""",
                (best["name"], best["name"], best["address"], best["postcode"],
                 best["city"], r["signal_id"]),
            )
            stats["attached"] += 1
    return stats
