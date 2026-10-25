#!/usr/bin/env python3
"""Export a filter-aware coverage matrix for DepotChargeDex.

What the site has to be able to answer
---------------------------------------
"Given the filters I have set (country, access class, power band, radius), what
share of the transport companies in range of a charging station is covered?"

The coverage therefore depends on THREE things at once:

  1. the RADIUS      (500 m, 1 km, 2 km, 5 km)
  2. the FILTER      (which chargers count: country, access class, power)
  3. the COMPANY SET (how many transport companies exist to be covered)

A company counts as covered when it is within the radius of at least one
charger that passes the filter.

Why the pairs are precomputed
------------------------------
The site must answer that question live, as the user moves a filter. Doing
the spatial join in the browser would need the full company set (158,863
points) and the full charger set in the page. So the join is done once, here,
and the result is shipped as a compact pair list.

For every charger-company pair within 5 km, the export stores:

    charger id, company id, distance (m), country, access class, power band

From that, the browser can compute, for any filter combination:

    covered companies = distinct company ids whose pair satisfies the filter

Because a company can be covered by several chargers, the counting must be a
DISTINCT count over the filtered pairs. That is exactly what the browser
does, so the browser number and the server number must agree. The exporter
prints both, for the default filter, so the two can be checked against each
other.

Sizes
-----
The pair list is bounded by the 5 km radius: 47,616 pairs today. Each pair is
a small JSON object. That is small enough to ship.

The company set is NOT shipped in the pair list; the company count per
country is shipped separately, so the denominator can be filtered by country
without downloading every company point.

Method
------
Rings are drawn around HGV-capable sites. The pair table is built from the
same premise set, so the map rings and the coverage numbers describe the
same chargers.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from depotmon.config import Settings  # noqa: E402
from depotmon.store import Store  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "webapp/public/data"

# The radii the site offers. 5 km is the outer bound of the pair table.
RADII = [500, 1000, 2000, 5000]

# The power bands the site filters on. The band a charger falls into decides
# which filter step includes it, so the pair table carries the band, not the
# raw kW: the browser then filters on a band without re-deriving it.
POWER_BANDS = [
    (0, 50),      # unknown or very small
    (50, 150),
    (150, 350),
    (350, 10 ** 9),
]

# The access classes the site filters on.
ACCESS_CLASSES = ["public", "semi_public", "private", "unknown"]


def power_band(kw: float | None) -> int:
    """Index into POWER_BANDS. A charger with no published power is band 0."""
    if kw is None:
        return 0
    for i, (lo, hi) in enumerate(POWER_BANDS):
        if lo <= kw < hi:
            return i
    return len(POWER_BANDS) - 1


def main() -> None:
    settings = Settings()
    store = Store(settings.database_url)
    OUT.mkdir(parents=True, exist_ok=True)
    cur = store.conn.cursor()

    # The charger side. premises_scores holds the scored sites; the country is
    # resolved the same way the map exporter resolves it, so the pair table
    # and the map agree about which country a charger is in.
    from depotmon.ingest.osm_eu import ISO3_TO_ISO2, country_of  # noqa: PLC0415

    # coverage_chargers is premises_scores UNION the AFIR HDV locations, so the
    # coverage counts the official EU charging sites as well as our own. Before
    # that view existed, a company 200 m from an AFIR charging pool counted as
    # uncovered. The view also resolves the country once, which is why the 300 m
    # signal subquery that used to live here is gone.
    cur.execute(
        """
        SELECT place_key, operator, tier, hgv_capable, power_kw,
               lon, lat, access_class, country_raw, source
          FROM coverage_chargers
        """
    )
    chargers = cur.fetchall()
    print(f"chargers: {len(chargers)}")
    _by_src: dict[str, int] = {}
    for _r in chargers:
        _by_src[_r.get("source") or "?"] = _by_src.get(_r.get("source") or "?", 0) + 1
    print("chargers by source:", ", ".join(f"{k}={v}" for k, v in sorted(_by_src.items())))

    # Resolve the country for every charger: the signal country first, then
    # the polygon country. The polygon wins over a bounding box, because a
    # box is an approximation of a border.
    charger_country: dict[str, str] = {}
    for row in chargers:
        code = (row["country_raw"] or "").strip().upper()
        # The country can arrive as ISO-2 ("DE") or ISO-3 ("DEU"), depending on
        # which feed declared it. The company layer is always ISO-2. If the two
        # do not agree, a German charger never matches a German company and the
        # coverage for Germany reads as zero. So both sides are normalised to
        # ISO-2 here, from the one mapping in osm_eu.
        code = ISO3_TO_ISO2.get(code, code if len(code) == 2 else "")
        if not code:
            code = country_of(float(row["lon"]), float(row["lat"]))
        if not code and 3.2 <= float(row["lon"]) <= 7.3 and 50.7 <= float(row["lat"]) <= 53.6:
            code = "NL"
        charger_country[row["place_key"]] = code

    charger_info = {
        row["place_key"]: {
            "operator": row["operator"],
            "tier": row["tier"],
            "hgv": bool(row["hgv_capable"]),
            "powerKw": float(row["power_kw"]) if row["power_kw"] else None,
            "band": power_band(float(row["power_kw"]) if row["power_kw"] else None),
            "access": row["access_class"] or "unknown",
            "country": charger_country[row["place_key"]],
            "source": row.get("source") or "osm",
        }
        for row in chargers
    }

    # The pair table. Every charger-company pair within 5 km.
    cur.execute(
        """
        SELECT p.place_key,
               c.company_id,
               c.country,
               ST_Distance(p.geog, c.geom::geography) AS dist
          FROM coverage_chargers p
          JOIN companies c
            ON ST_DWithin(p.geog, c.geom::geography, 5000)
         WHERE p.lon IS NOT NULL AND p.lat IS NOT NULL
           AND c.geom IS NOT NULL
        """
    )
    pairs = cur.fetchall()
    print(f"pairs within 5 km: {len(pairs)}")

    # The company set: the denominator. Per country, how many transport
    # companies exist. The company must be in the same country as the charger
    # for the coverage to be that country's fleet; a company 2 km from a
    # German charger in Belgium is a Belgian company.
    cur.execute(
        """
        SELECT COALESCE(NULLIF(country, ''), '') AS cc, count(*) AS n
          FROM companies
         WHERE geom IS NOT NULL
         GROUP BY 1
        """
    )
    _raw_by_country = {r["cc"]: r["n"] for r in cur.fetchall()}
    # Same normalisation as the company table below, so the denominator uses
    # the same country keys the pairs use.
    companies_by_country: dict[str, int] = {}
    for cc, n in _raw_by_country.items():
        code = ISO3_TO_ISO2.get(cc, cc if len(cc) == 2 else "")
        companies_by_country[code] = companies_by_country.get(code, 0) + n
    companies_total = sum(companies_by_country.values())
    print(f"companies: {companies_total} across {len(companies_by_country)} countries")

    # Build the pair list for the browser. Each entry is
    # [chargerIndex, companyIndex, distanceMetres]. The charger and company
    # attributes are shipped in separate tables, so the browser never has to
    # carry a repeated string per pair.
    charger_ids = sorted(charger_info)
    charger_index = {cid: i for i, cid in enumerate(charger_ids)}

    # Companies are indexed by (country, id) so the browser can filter the
    # denominator by country without a separate lookup.
    cur.execute(
        "SELECT company_id, COALESCE(NULLIF(country, ''), '') AS cc FROM companies WHERE geom IS NOT NULL"
    )
    company_country: dict[int, str] = {r["company_id"]: r["cc"] for r in cur.fetchall()}
    # The company country can also arrive as ISO-3. Normalise to ISO-2, the
    # same shape the charger side uses, so the two sides can be compared.
    company_country = {
        cid: (ISO3_TO_ISO2.get(cc, cc if len(cc) == 2 else ""))
        for cid, cc in company_country.items()
    }
    company_ids = sorted(company_country)
    company_index = {cid: i for i, cid in enumerate(company_ids)}

    pairs_out = []
    for row in pairs:
        ci = charger_index.get(row["place_key"])
        mi = company_index.get(row["company_id"])
        if ci is None or mi is None:
            continue
        pairs_out.append([ci, mi, int(round(float(row["dist"])))])

    # Column 6 is the source, 0 = our own scored sites, 1 = the AFIR layer.
    # Without it the coverage number silently includes 6,085 official EU sites
    # and nobody can tell how much of the total comes from where.
    charger_table = [
        [
            charger_info[cid]["country"],
            charger_info[cid]["access"],
            charger_info[cid]["band"],
            1 if charger_info[cid]["hgv"] else 0,
            charger_info[cid]["tier"],
            1 if charger_info[cid].get("source") == "tentec" else 0,
        ]
        for cid in charger_ids
    ]

    # Per charger, how many transport companies are within each radius. This is
    # the "how many companies are in range of THIS charging station" number the
    # popup shows. It is a count of pairs, not of distinct companies, because a
    # single charger cannot cover the same company twice.
    charger_company_counts = [[0] * len(RADII) for _ in charger_ids]
    for ci, _mi, dist in pairs_out:
        row = charger_company_counts[ci]
        for ri, r in enumerate(RADII):
            if dist <= r:
                row[ri] += 1

    (OUT / "coverage_pairs.json").write_text(
        json.dumps(
            {
                "radii": RADII,
                "powerBands": [{"lo": lo, "hi": hi if hi < 10 ** 9 else None} for lo, hi in POWER_BANDS],
                "accessClasses": ACCESS_CLASSES,
                "chargers": charger_table,
                "chargerCompanyCounts": charger_company_counts,
                "pairs": pairs_out,
                "companiesByCountry": companies_by_country,
                "companiesTotal": companies_total,
            },
            separators=(",", ":"),
        )
    )
    size = (OUT / "coverage_pairs.json").stat().st_size
    _n_afir = sum(r[5] for r in charger_table)
    print(f"chargers from AFIR: {_n_afir}, from own sources: {len(charger_table) - _n_afir}")
    print(f"wrote coverage_pairs.json: {len(charger_table)} chargers, "
          f"{len(company_ids)} companies, {len(pairs_out)} pairs, {size // 1024} KB")

    # Cross-check: the browser will compute the same numbers. Print the
    # default filter (all chargers, 500 m) computed here, so the two can be
    # compared after the site is live.
    def compute(radius: int, countries: set[str] | None = None,
                access: set[str] | None = None, bands: set[int] | None = None,
                hgv_only: bool = False) -> dict:
        covered: set[int] = set()
        for ci, mi, dist in pairs_out:
            if dist > radius:
                continue
            cc, ac, band, hgv, _tier, _src = charger_table[ci]
            if countries is not None and cc not in countries:
                continue
            if access is not None and ac not in access:
                continue
            if bands is not None and band not in bands:
                continue
            if hgv_only and not hgv:
                continue
            covered.add(mi)
        denom = 0
        for cc, n in companies_by_country.items():
            if countries is not None and cc not in countries:
                continue
            denom += n
        return {
            "covered": len(covered),
            "denominator": denom,
            "pct": round(100.0 * len(covered) / denom, 1) if denom else None,
        }

    print("\ncross-check (server side, same maths the browser will run):")
    for radius in RADII:
        r = compute(radius)
        print(f"  radius {radius:>4} m: {r['covered']:>6} / {r['denominator']:>6} "
              f"= {r['pct']}%")
    r = compute(500, hgv_only=True)
    print(f"  radius  500 m, HGV only: {r['covered']} / {r['denominator']} = {r['pct']}%")
    de = compute(500, countries={"DE"})
    print(f"  radius  500 m, DE only: {de['covered']} / {de['denominator']} = {de['pct']}%")


if __name__ == "__main__":
    main()
