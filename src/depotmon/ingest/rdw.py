"""Ingest: RDW open data — monthly first registrations of zero-emission N2/N3 trucks.

RDW has no cross-dataset join, so the fuel join is done client-side: vehicle rows for a
month are fetched, then the fuel table is queried with `kenteken in (...)` in batches.
"""

from __future__ import annotations

import calendar
from datetime import date

from ..config import Settings
from ..fetch import Fetcher
from ..store import Store, as_json

VEHICLES = "https://opendata.rdw.nl/resource/m9d7-ebf2.json"
FUEL = "https://opendata.rdw.nl/resource/8ys7-d773.json"
BATCH = 100
MONTHS_METRIC = "rdw_n2n3_first_registrations"
ZE_METRIC = "rdw_ze_n2n3_first_registrations"


def month_keys(months: int) -> list[str]:
    today = date.today()
    out = []
    for i in range(months - 1, -1, -1):
        y = today.year
        m = today.month - i
        while m <= 0:
            m += 12
            y -= 1
        out.append(f"{y}-{m:02d}")
    return out


def _month_bounds(period: str) -> tuple[str, str]:
    y, m = (int(x) for x in period.split("-"))
    last = calendar.monthrange(y, m)[1]
    return f"{y}{m:02d}01", f"{y}{m:02d}{last:02d}"


def _fetch_month(fetcher: Fetcher, period: str) -> list[dict]:
    lo, hi = _month_bounds(period)
    resp = fetcher.get(
        VEHICLES,
        params={
            "$select": "kenteken,europese_voertuigcategorie,datum_eerste_toelating",
            "$where": (
                'europese_voertuigcategorie in("N2","N3") '
                f'AND datum_eerste_toelating between "{lo}" and "{hi}"'
            ),
            "$limit": 50000,
        },
    )
    if resp.status != 200:
        raise RuntimeError(f"RDW vehicles HTTP {resp.status}")
    return resp.json()


def _fetch_fuels(fetcher: Fetcher, kentekens: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for i in range(0, len(kentekens), BATCH):
        chunk = kentekens[i : i + BATCH]
        quoted = ",".join(f'"{k}"' for k in chunk)
        resp = fetcher.get(
            FUEL,
            params={
                "$select": "kenteken,brandstof_omschrijving",
                "$where": f"kenteken in({quoted})",
                "$limit": len(chunk) * 3,
            },
        )
        if resp.status != 200:
            raise RuntimeError(f"RDW fuel HTTP {resp.status}")
        for row in resp.json():
            out.setdefault(row["kenteken"], set()).add(row.get("brandstof_omschrijving") or "")
    return out


def run(store: Store, settings: Settings, *, months: int | None = None,
        refresh: bool = False) -> dict:
    months = months or settings.rdw_months
    run_id = store.start_run("ingest", "rdw")
    stats = {"months": [], "requests": 0, "ze_total": 0, "total": 0, "skipped": 0}
    try:
        existing = {r["period"] for r in store.fleet_series(MONTHS_METRIC)}
        with Fetcher(settings, store=store, run_id=run_id, source="rdw") as fetcher:
            for period in month_keys(months):
                if period in existing and not refresh:
                    stats["skipped"] += 1
                    continue
                vehicles = _fetch_month(fetcher, period)
                kentekens = sorted({v["kenteken"] for v in vehicles if v.get("kenteken")})
                fuels = _fetch_fuels(fetcher, kentekens) if kentekens else {}
                ze = sum(
                    1
                    for k in kentekens
                    if any("elektriciteit" in f.lower() for f in fuels.get(k, set()))
                )
                store.upsert_fleet(MONTHS_METRIC, period, "NL", len(kentekens), "rdw")
                store.upsert_fleet(ZE_METRIC, period, "NL", ze, "rdw")
                stats["months"].append({"period": period, "total": len(kentekens), "ze": ze})
                stats["total"] += len(kentekens)
                stats["ze_total"] += ze
        store.record_health("rdw", True, len(stats["months"]))
        store.finish_run(run_id, status="ok", records_in=stats["total"],
                         records_out=stats["ze_total"], note=as_json(stats))
        return stats
    except Exception as exc:
        store.record_health("rdw", False, 0, error=str(exc)[:500])
        store.alert("source_failure", f"rdw ingest failed: {exc}", source="rdw")
        store.finish_run(run_id, status="failed", error=str(exc)[:2000])
        raise
