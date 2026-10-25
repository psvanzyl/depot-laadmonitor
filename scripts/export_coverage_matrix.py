#!/usr/bin/env python3
"""Publish the coverage pair matrix for 1, 2.5, 5, 10 and 20 km.

The coverage used to be a single JSON file holding every pair within 5 km, with
a distance on each pair. That cannot reach 20 km: measured on this database the
pair set grows 25k (1 km), 95k (2.5 km), 259k (5 km), 731k (10 km) and 2.07M
(20 km), and 2.07M pairs as JSON is about 37 MB. Querying the database per
slider move is about a second, which is not a filter.

So each radius gets its own file, each pair is one 32-bit integer, and the
radius is implicit in which file you loaded:

    (chargerIndex << 18) | companyIndex

18 bits holds 262,143 companies (there are 230,904) and 14 bits holds 16,383
chargers (there are 9,021). Four bytes per pair:

    1 km    100 KB      2.5 km   380 KB     5 km   1.0 MB
    10 km   2.9 MB      20 km    8.3 MB   (before transfer compression)

The browser fetches only the radius that is selected, so picking 1 km costs
100 KB and 20 km costs 8.3 MB once. That is also why distance stops being a
column: a 1 km file cannot contain a 2 km pair, so there is nothing to check.

The charger attributes and the per-country denominators go in one small JSON
that every radius shares.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg
from psycopg.rows import dict_row

from depotmon.config import Settings
from depotmon.ingest.osm_eu import ISO3_TO_ISO2

OUT = Path(__file__).resolve().parents[1] / "webapp/public/data"

RADII = [5000, 10000, 20000]
# Four bands, matching the chips in the Dashboard exactly. These used to
# disagree: the data had three and the UI had four, so band 0 was
# "50-150 kW" in the data and "<50 kW" on screen. Clicking "<50 kW"
# counted 50-150 kW chargers.
POWER_BANDS = [(0, 50), (50, 150), (150, 350), (350, 10 ** 9)]
ACCESS_CLASSES = ["public", "semi_public", "private", "unknown"]

MAGIC = b"DCDX"
VERSION = 1
# 24 bytes, deliberately: a Uint32Array cannot start at a byte offset that is
# not a multiple of four, and copying an 8 MB payload to satisfy the alignment
# is a waste when the header can simply be padded.
HEADER = struct.Struct("<4sBHI I I 5x")  # magic, version, radius, nChargers, nCompanies, nPairs
COMPANY_BITS = 18
COMPANY_MASK = (1 << COMPANY_BITS) - 1


def power_band(kw: float | None) -> int:
    if kw is None:
        return 0
    for i, (lo, hi) in enumerate(POWER_BANDS):
        if lo <= kw < hi:
            return i
    return len(POWER_BANDS) - 1


def main() -> None:
    settings = Settings()
    OUT.mkdir(parents=True, exist_ok=True)

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        cur = conn.cursor()

        # Chargers, in a stable order so the index in the binary matches the
        # index in the attribute table.
        cur.execute("""
            SELECT ctid::text AS rid, operator, tier, hgv_capable, power_kw,
                   lon, lat, access_class, country_raw, source
              FROM coverage_chargers
             ORDER BY ctid::text
        """)
        chargers = cur.fetchall()
        print(f"chargers: {len(chargers):,}")
        if len(chargers) > (1 << 14):
            raise SystemExit("charger index needs more than 14 bits")

        # Keyed by ctid, not place_key: 202 place_keys are shared by two
        # unrelated chargers (a Norwegian 400 kW site and a Spanish one are both
        # osm:0198), so a place_key lookup silently gave one of them the
        # other one's power, tier and access.
        charger_index = {r["rid"]: i for i, r in enumerate(chargers)}

        def country_of(r) -> str:
            cc = (r["country_raw"] or "").strip().upper()
            if len(cc) == 3:
                cc = ISO3_TO_ISO2.get(cc, "")
            return cc if len(cc) == 2 else ""

        # Columns 6 and 7 are the coordinates: the browser draws the radius
        # circle itself, so that the ring always describes the chargers the
        # filter selected and the radius that is set, instead of a separate
        # exported polygon file that can disagree with both.
        charger_table = [
            [
                country_of(r),
                (r["access_class"] or "unknown"),
                power_band(float(r["power_kw"]) if r["power_kw"] else None),
                1 if r["hgv_capable"] else 0,
                r["tier"] or "C",
                1 if r["source"] == "tentec" else 0,
                round(float(r["lon"]), 5),
                round(float(r["lat"]), 5),
            ]
            for r in chargers
        ]

        # Companies, also in a stable order.
        cur.execute("""
            SELECT company_id, COALESCE(NULLIF(country, ''), '') AS cc
              FROM companies
             WHERE geom IS NOT NULL
             ORDER BY company_id
        """)
        companies = cur.fetchall()
        print(f"companies: {len(companies):,}")
        if len(companies) > (1 << COMPANY_BITS):
            raise SystemExit("company index needs more than 18 bits")

        company_index = {r["company_id"]: i for i, r in enumerate(companies)}
        companies_by_country: dict[str, int] = {}
        for r in companies:
            cc = (r["cc"] or "").strip().upper()
            if len(cc) == 3:
                cc = ISO3_TO_ISO2.get(cc, "")
            if len(cc) == 2:
                companies_by_country[cc] = companies_by_country.get(cc, 0) + 1

        meta = {
            "radii": RADII,
            "powerBands": [{"lo": lo, "hi": hi if hi < 10 ** 9 else None}
                           for lo, hi in POWER_BANDS],
            "accessClasses": ACCESS_CLASSES,
            "companyBits": COMPANY_BITS,
            "chargers": charger_table,
            "companiesByCountry": companies_by_country,
            "companiesTotal": len(companies),
            "magic": MAGIC.decode(),
            "version": VERSION,
        }
        (OUT / "coverage_meta.json").write_text(
            json.dumps(meta, separators=(",", ":"), ensure_ascii=False))
        print(f"coverage_meta.json: {(OUT / 'coverage_meta.json').stat().st_size / 1024:.0f} KB")

        # One binary per radius.
        for radius in RADII:
            cur.execute(
                """
                SELECT p.ctid::text AS rid, c.company_id
                  FROM coverage_chargers p
                  JOIN companies c
                    ON ST_DWithin(p.geog, c.geom::geography, %s)
                 WHERE c.geom IS NOT NULL
                 ORDER BY p.ctid::text, c.company_id
                """,
                (radius,),
            )
            rows = cur.fetchall()
            packed = bytearray()
            count = 0
            for r in rows:
                ci = charger_index.get(r["rid"])
                mi = company_index.get(r["company_id"])
                if ci is None or mi is None:
                    continue
                packed += (ci << COMPANY_BITS | mi).to_bytes(4, "little")
                count += 1

            header = HEADER.pack(MAGIC, VERSION, radius, len(chargers),
                                 len(companies), count)
            path = OUT / f"coverage_{radius}.bin"
            path.write_bytes(header + bytes(packed))
            print(f"  coverage_{radius}.bin  {count:>9,} pairs  "
                  f"{path.stat().st_size / 1e6:>6.2f} MB")


if __name__ == "__main__":
    main()
