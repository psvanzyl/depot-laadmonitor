"""Ingest: CBS StatLine 85239NED — active business vehicles by fuel and province.

Used as *context* (is the regional fleet electrifying?), never as a site signal:
the table has no municipality detail and no charging information.
"""

from __future__ import annotations

from ..config import Settings
from ..fetch import Fetcher
from ..store import Store, as_json

TABLE = "85239NED"
BASE = f"https://opendata.cbs.nl/ODataFeed/odata/{TABLE}"

TRUCK_TYPES = {"A018936": "Vrachtauto", "A018937": "Trekker voor oplegger"}
BUILD_YEARS_TOTAL = "T001378"
METRIC = "cbs_ze_bedrijfsvoertuigen_stock"

PROVINCES = {
    "Groningen_2": "Groningen",
    "Fryslan_3": "Fryslân",
    "Drenthe_4": "Drenthe",
    "Overijssel_5": "Overijssel",
    "Flevoland_6": "Flevoland",
    "Gelderland_7": "Gelderland",
    "Utrecht_8": "Utrecht",
    "NoordHolland_9": "Noord-Holland",
    "ZuidHolland_10": "Zuid-Holland",
    "Zeeland_11": "Zeeland",
    "NoordBrabant_12": "Noord-Brabant",
    "Limburg_13": "Limburg",
}


def _electric_key(row: dict) -> str | None:
    for key in row:
        if key.startswith("Elektriciteit"):
            return key
    return None


def run(store: Store, settings: Settings) -> dict:
    run_id = store.start_run("ingest", "cbs")
    stats = {"rows": 0, "periods": [], "written": 0}
    try:
        with Fetcher(settings, store=store, run_id=run_id, source="cbs") as fetcher:
            resp = fetcher.get(
                f"{BASE}/TypedDataSet",
                params={
                    "$filter": (
                        "(" + " or ".join(f"Voertuigtype eq '{k}'" for k in TRUCK_TYPES) + ")"
                        f" and Bouwjaren eq '{BUILD_YEARS_TOTAL}'"
                    ),
                    "$format": "json",
                },
            )
            if resp.status != 200:
                raise RuntimeError(f"CBS HTTP {resp.status}")
            rows = resp.json().get("value", [])
        stats["rows"] = len(rows)
        periods = set()
        for row in rows:
            ekey = _electric_key(row)
            if not ekey:
                continue
            period = str(row.get("Perioden") or "")[:4]
            if not period.isdigit():
                continue
            periods.add(period)
            total = 0
            for col, name in PROVINCES.items():
                val = row.get(col)
                if val is None:
                    continue
                store.upsert_fleet(METRIC, period, name, float(val), "cbs")
                total += float(val)
                stats["written"] += 1
            store.upsert_fleet(METRIC, period, "NL", total, "cbs")
        stats["periods"] = sorted(periods)
        store.record_health("cbs", True, len(rows))
        store.finish_run(run_id, status="ok", records_in=len(rows),
                         records_out=stats["written"], note=as_json(stats))
        return stats
    except Exception as exc:
        store.record_health("cbs", False, 0, error=str(exc)[:500])
        store.alert("source_failure", f"cbs ingest failed: {exc}", source="cbs")
        store.finish_run(run_id, status="failed", error=str(exc)[:2000])
        raise
