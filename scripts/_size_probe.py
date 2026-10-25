"""Size test: how big is the 20 km pair set in a compact binary form?

Server-side gives about 1 s per query, which is too slow for something that
recalculates on every slider move. A precomputed file keeps it instant, but JSON
at 2.07M pairs is ~37 MB, so the encoding decides it.

Layout, grouped by charger so the deltas stay small:

    varint  chargers in this block
    per charger: varint chargerIndexDelta, varint pairCount
      per pair:  varint companyIndexDelta (sorted within the charger), byte dist/100m

Run it, then judge: under about 4 MB gzipped and this ships as a lazy fetch.
Over that, the server API is the only honest option.
"""
from __future__ import annotations

import gzip
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg
from psycopg.rows import dict_row

from depotmon.config import Settings

MAX_R = 20000


def varint(n: int, out: bytearray) -> None:
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def main() -> None:
    settings = Settings()
    t0 = time.time()
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.place_key,
                       c.company_id,
                       ST_Distance(p.geog, c.geom::geography) AS dist
                  FROM coverage_chargers p
                  JOIN companies c
                    ON ST_DWithin(p.geog, c.geom::geography, %s)
                 WHERE c.geom IS NOT NULL
                 ORDER BY p.place_key, c.company_id
            """, (MAX_R,))
            rows = cur.fetchall()
    print(f"fetched {len(rows):,} pairs in {time.time() - t0:.1f}s")

    # charger index
    charger_ids: dict[str, int] = {}
    company_ids: dict[int, int] = {}
    for r in rows:
        charger_ids.setdefault(r["place_key"], len(charger_ids))
        company_ids.setdefault(r["company_id"], len(company_ids))
    print(f"chargers {len(charger_ids):,}, companies {len(company_ids):,}")

    out = bytearray()
    i = 0
    n = len(rows)
    prev_charger = -1
    blocks = 0
    while i < n:
        key = rows[i]["place_key"]
        ci = charger_ids[key]
        j = i
        while j < n and rows[j]["place_key"] == key:
            j += 1
        varint(ci - prev_charger, out)
        prev_charger = ci
        varint(j - i, out)
        prev_company = 0
        for k in range(i, j):
            co = company_ids[rows[k]["company_id"]]
            varint(co - prev_company, out)
            prev_company = co
            d = int(rows[k]["dist"] // 100)
            out.append(min(d, 255))
        blocks += 1
        i = j

    raw = len(out)
    comp = len(gzip.compress(bytes(out), 6))
    print(f"blocks {blocks:,}")
    print(f"raw   {raw / 1e6:.2f} MB")
    print(f"gzip  {comp / 1e6:.2f} MB")
    print("VERDICT:", "ship a lazy binary file" if comp < 4_500_000
          else "too big; use a server API")


if __name__ == "__main__":
    main()
