"""Classify: status + confidence per site, from the evidence rows attached to it."""

from __future__ import annotations

import hashlib
import json
import re

from .. import textutils as tu
from ..config import Settings
from ..store import Store

# higher wins when several statuses are supported
PRECEDENCE = [
    "operational",
    "under_construction",
    "grid_connection_signalled",
    "permit_granted",
    "permit_requested",
    "rumoured",
    "unknown",
]

CONSTRUCTION_RE = re.compile(
    r"in aanbouw|bouw gestart|wordt gebouwd|realisatie|werkzaamheden|aanleg van|"
    r"gereed|in gebruik genomen|geopend",
    re.IGNORECASE,
)


def status_rank(status: str) -> int:
    try:
        return len(PRECEDENCE) - PRECEDENCE.index(status)
    except ValueError:
        return 0


def decide(statuses: list[str]) -> str:
    best = "unknown"
    for s in statuses:
        if status_rank(s) > status_rank(best):
            best = s
    return best


def confidence_for(evidence: list[dict], status: str, *, has_address: bool,
                   has_power: bool, multi_source: bool) -> float:
    """Heuristic, deterministic, explainable. No LLM involved."""
    sources = {e["source"] for e in evidence}
    conf = 0.35
    conf += min(0.30, 0.12 * max(0, len(sources) - 1))       # corroboration
    if status in {"operational", "permit_granted"}:
        conf += 0.15
    elif status == "permit_requested":
        conf += 0.05
    elif status == "rumoured":
        conf -= 0.10
    if has_address:
        conf += 0.08
    if has_power:
        conf += 0.07
    if multi_source:
        conf += 0.03
    return round(max(0.05, min(0.95, conf)), 2)


def state_hash(site: dict) -> str:
    payload = {
        "status": site["status"],
        "operator": (site.get("operator") or "").lower(),
        "power_kw_announced": float(site["power_kw_announced"] or 0),
        "power_kw_operational": float(site["power_kw_operational"] or 0),
        "n_points": site.get("n_points") or 0,
        "hgv_capable": bool(site.get("hgv_capable")),
        "access": site.get("access"),
        "confidence": float(site.get("confidence") or 0),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def run(store: Store, settings: Settings) -> dict:
    run_id = store.start_run("classify")
    stats = {"sites": 0, "by_status": {}, "llm_used": 0}
    llm = None
    if settings.llm_classify:
        from .llm import PermitClassifier

        llm = PermitClassifier(settings, store)
    try:
        with store.conn.cursor() as cur:
            cur.execute("SELECT * FROM sites")
            sites = list(cur.fetchall())
        stats["sites"] = len(sites)
        for site in sites:
            with store.conn.cursor() as cur:
                cur.execute(
                    "SELECT source, url, excerpt, observed_at FROM evidence WHERE site_id=%s",
                    (site["site_id"],),
                )
                evidence = list(cur.fetchall())
            statuses = [
                tu.status_from_text(e["excerpt"] or "") for e in evidence
                if e["source"] == "permits"
            ]
            # a charge point in the registers means the hardware exists and is in service
            if any(e["source"] in {"ndw", "osm"} for e in evidence):
                statuses.append("operational")
            status = decide([s for s in statuses if s] or ["unknown"])

            if llm is not None:
                permit_texts = [
                    e["excerpt"] for e in evidence
                    if e["source"] == "permits" and e["excerpt"]
                ]
                vote = llm.classify_site(permit_texts)
                if vote:
                    stats["llm_used"] += 1
                    if status_rank(vote) > status_rank(status) and vote != "operational":
                        status = vote

            conf = confidence_for(
                evidence,
                status,
                has_address=bool(site["address"]),
                has_power=bool(site["power_kw_announced"] or site["power_kw_operational"]),
                multi_source=len({e["source"] for e in evidence}) > 1,
            )
            updated = dict(site)
            updated["status"] = status
            updated["confidence"] = conf
            shash = state_hash(updated)
            for e in evidence:
                if e["source"] == "permits" and CONSTRUCTION_RE.search(e["excerpt"] or ""):
                    pass  # construction wording is already captured by status_from_text
            with store.tx() as cur:
                cur.execute(
                    "UPDATE sites SET status=%s, confidence=%s, state_hash=%s, updated_at=now()"
                    " WHERE site_id=%s",
                    (status, conf, shash, site["site_id"]),
                )
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
        store.finish_run(run_id, status="ok", records_in=len(sites), note=json.dumps(stats))
        return stats
    except Exception as exc:
        store.finish_run(run_id, status="failed", error=str(exc)[:2000])
        raise
