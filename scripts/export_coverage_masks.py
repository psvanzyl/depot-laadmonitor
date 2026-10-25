#!/usr/bin/env python3
"""Coverage masks for the long radii.

Boss asked for 5 / 10 / 20 / 50 / 100 km. The existing pair files cannot carry
the long end:

    50 km    9,045,272 pairs   34.5 MB packed
    100 km  29,723,296 pairs  113.4 MB packed

113 MB is not a filter. But the question at 50 km is not "which charger reaches
which company" — it is "is there ANY charger of this class within range". So the
index inverts: one record per company holding a bitmask of the charger CLASSES
that reach it.

A class is the full filter tuple (tier, access, power band, source, hgv). There
are ~65 distinct classes among the 9,266 chargers, so a filter is a bitmask too
and "covered" is one AND per company. That is exact, and it is why one file
answers every filter combination.

File layout:

    header   4s B I H I H B 3x   magic, version, radius, nClasses, nRecords,
                                 nCountries, wordsPerRecord
    classes  nClasses x 5 bytes   tier, access, band, source, hgv
    country  nCountries x 2 chars ASCII codes
    records  nRecords x 4 u32     countryIdx, mask0, mask1, mask2

Bit layout, 65 classes over three words:

    mask0   classes  0-31
    mask1   classes 32-61
    mask2   classes 62-64

Only covered companies get a record: an all-zero mask contributes to no count.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from depotmon.config import Settings  # noqa: E402

OUT = Path("webapp/public/data")
MAGIC = b"DCDM"
VERSION = 1
WORDS = 3
RADII = [50_000, 100_000]
HEADER = struct.Struct("<4sBIHIHB3x")

# Must match scripts/export_coverage_matrix.py and the UI chips.
POWER_BANDS = [(0, 50), (50, 150), (150, 350), (350, 10**9)]
ACCESS = ["public", "semi_public", "private", "unknown"]


def band_of(kw: float | None) -> int | None:
    if kw is None:
        return None
    for i, (lo, hi) in enumerate(POWER_BANDS):
        if lo <= kw < hi:
            return i
    return len(POWER_BANDS) - 1


def main() -> int:
    settings = Settings()
    OUT.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        cur = conn.cursor()

        cur.execute("""
            SELECT ctid::text AS rid, tier, access_class, hgv_capable, power_kw, source
              FROM coverage_chargers
        """)
        chargers = [dict(r) for r in cur.fetchall()]
        print(f"chargers: {len(chargers):,}")

        def key(r):
            """(tier letter, access name, hgv 0/1, source 0/1, power band)."""
            return (
                (r["tier"] or "C"),
                (r["access_class"] or "unknown"),
                1 if r["hgv_capable"] else 0,
                1 if r["source"] == "tentec" else 0,
                band_of(float(r["power_kw"]) if r["power_kw"] is not None else None),
            )

        def sort_key(t):
            tier, access, hgv, src, band = t
            return (tier, ACCESS.index(access) if access in ACCESS else 9,
                    src, hgv, -1 if band is None else band)

        distinct = {key(r) for r in chargers}
        ordered = sorted(distinct, key=sort_key)
        cls_of = {t: i for i, t in enumerate(ordered)}
        n_classes = len(ordered)
        print(f"charger classes: {n_classes}")
        if n_classes > WORDS * 32:
            raise SystemExit(f"{n_classes} classes need more than {WORDS * 32} bits")

        # Keyed by ctid, not place_key: 202 place_keys are shared by two
        # unrelated chargers (a Norwegian 400 kW site and a Spanish one both
        # called osm:0198), so place_key is not unique in this table.
        cur.execute("CREATE TEMP TABLE charger_class (rid text PRIMARY KEY, cls int)")
        with cur.copy("COPY charger_class (rid, cls) FROM STDIN") as cp:
            for r in chargers:
                cp.write_row((r["rid"], cls_of[key(r)]))
        cur.execute("ANALYZE charger_class")

        cur.execute("SELECT company_id AS id, country FROM companies WHERE geom IS NOT NULL")
        country_of = {r["id"]: (r["country"] or "??") for r in cur.fetchall()}
        countries = sorted(set(country_of.values()))
        cidx = {c: i for i, c in enumerate(countries)}
        print(f"companies: {len(country_of):,} in {len(countries)} countries")

        class_block = bytearray()
        for t in ordered:
            tier, access, hgv, src, band = t
            class_block += bytes(
                [
                    ord(tier[0]),                 # 'A' -> 65, 'B' -> 66, 'C' -> 67
                    ACCESS.index(access) if access in ACCESS else 255,
                    255 if band is None else band,
                    src,
                    hgv,
                ]
            )
        country_block = b"".join(c.encode()[:2].ljust(2, b" ") for c in countries)

        for radius in RADII:
            cur.execute(
                """
                SELECT co.company_id AS cid,
                       bit_or(CASE WHEN cc.cls < 32 THEN (1::bigint << cc.cls)
                                   ELSE 0 END) AS w0,
                       bit_or(CASE WHEN cc.cls >= 32 AND cc.cls < 62
                                   THEN (1::bigint << (cc.cls - 32)) ELSE 0 END) AS w1,
                       bit_or(CASE WHEN cc.cls >= 62
                                   THEN (1::bigint << (cc.cls - 62)) ELSE 0 END) AS w2
                  FROM coverage_chargers p
                  JOIN companies co
                    ON ST_DWithin(p.geog, co.geom::geography, %s)
                  JOIN charger_class cc
                    ON cc.rid = p.ctid::text
                 GROUP BY co.company_id
                """,
                (radius,),
            )
            rows = cur.fetchall()
            payload = bytearray()
            for r in rows:
                payload += struct.pack(
                    "<IIII",
                    cidx[country_of[r["cid"]]],
                    r["w0"] & 0xFFFFFFFF,
                    r["w1"] & 0xFFFFFFFF,
                    r["w2"] & 0xFFFFFFFF,
                )
            header = HEADER.pack(
                MAGIC, VERSION, radius, n_classes, len(rows), len(countries), WORDS
            )
            path = OUT / f"coverage_mask_{radius}.bin"
            blob = header + bytes(class_block) + country_block + bytes(payload)
            path.write_bytes(blob)
            print(f"  {path.name:26} {len(rows):>7,} companies  {len(blob):>10,} bytes")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
