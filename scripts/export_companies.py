"""Write the company base layer for the map (transport/logistics premises in NL)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from depotmon.config import Settings
from depotmon.store import Store

OUT = Path("webapp/public/data")


def main() -> int:
    settings = Settings()
    feats = []
    with Store(settings.database_url) as store:
        with store.conn.cursor() as cur:
            cur.execute(
                """SELECT name, kind, address, postcode, city, source, source_url,
                          country, ST_X(geom) AS lon, ST_Y(geom) AS lat
                     FROM companies
                    WHERE geom IS NOT NULL
                      -- Some OSM ways carry a struck-through or default 0,0-ish
                      -- location: 14 points landed in Mexico and Africa.
                      AND ST_X(geom) BETWEEN -25 AND 45
                      AND ST_Y(geom) BETWEEN 34 AND 72
                    ORDER BY name"""
            )
            rows = cur.fetchall()
    for r in rows:
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                "properties": {
                    "name": r["name"],
                    "kind": r["kind"],
                    "address": r["address"],
                    "postcode": r["postcode"],
                    "city": r["city"],
                    "source": r["source"],
                    "sourceUrl": r["source_url"],
                    "country": r["country"],
                },
            }
        )
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {"type": "FeatureCollection", "features": feats}
    (OUT / "companies.geojson").write_text(json.dumps(payload, ensure_ascii=False))
    print(json.dumps({"companies": len(feats)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
