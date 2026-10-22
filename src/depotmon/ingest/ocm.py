"""Ingest: OpenChargeMap (disabled unless OCM_API_KEY is set).

Recon result: the public endpoint answers HTTP 403 without a key (SOURCES.md #9).
This module exists so an operator with a key can switch it on without code changes.
"""

from __future__ import annotations

from ..config import Settings
from ..fetch import Fetcher
from ..store import Store, as_json

URL = "https://api.openchargemap.io/v3/poi/"


def run(store: Store, settings: Settings) -> dict:
    if not settings.ocm_api_key:
        store.alert(
            "source_failure",
            "OpenChargeMap skipped: OCM_API_KEY not configured (HTTP 403 without a key)",
            source="ocm",
        )
        store.record_health("ocm", False, 0, error="no api key")
        return {"skipped": True, "reason": "no OCM_API_KEY"}
    run_id = store.start_run("ingest", "ocm")
    stats = {"records": 0, "kept": 0, "new": 0, "updated": 0, "unchanged": 0}
    try:
        with Fetcher(settings, store=store, run_id=run_id, source="ocm") as fetcher:
            resp = fetcher.get(
                URL,
                params={
                    "output": "json",
                    "countrycode": "NL",
                    "maxresults": 5000,
                    "key": settings.ocm_api_key,
                    "compact": "true",
                    "verbose": "false",
                },
            )
            if resp.status != 200:
                raise RuntimeError(f"OCM HTTP {resp.status}")
            data = resp.json()
        stats["records"] = len(data)
        store.record_health("ocm", True, len(data))
        store.finish_run(run_id, status="ok", records_in=len(data), records_out=0,
                         note=as_json(stats))
        return stats
    except Exception as exc:
        store.record_health("ocm", False, 0, error=str(exc)[:500])
        store.alert("source_failure", f"ocm ingest failed: {exc}", source="ocm")
        store.finish_run(run_id, status="failed", error=str(exc)[:2000])
        raise
