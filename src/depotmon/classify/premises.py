"""Classify: private-premises charging likelihood (evidence stacking, not a register).

There is no public register of private depot chargers. So instead of pretending a list
exists, this step stacks independent, attributable evidence per business premises and
publishes a *tier* with the reasons attached:

    A  verified    — operational claim (own laadplein in use) or granted permit,
                     plus a located address (postcode) so somebody can check it.
    B  probable    — explicit plan/construction/award, or a permit, located.
    C  candidate   — named site without a verifiable address, or thin evidence.

Every row carries its evidence URLs, so a human can verify in one click. The model is
deliberately conservative: correlated items from the same channel never add up linearly,
and public charge-point evidence *lowers* the score (that is a different product).

Sources: news (trade press), permits (Officiële Bekendmakingen), osm (private/hgv tags),
ndw (public CPO network — used as counter-evidence only).
"""

from __future__ import annotations

import json
import re
from datetime import date

from .. import news_extract as nx
from ..config import Settings
from ..store import Store, as_json

# Weight per channel and lifecycle/status. These are priors, not measurements: they
# encode "how often does this signal, on its own, mean a private HGV charging site".
WEIGHTS: dict[str, dict[str, float]] = {
    "news": {
        "opening": 0.88,       # "heeft een eigen laadplein in gebruik genomen"
        "expansion": 0.82,     # existing site grows
        "construction": 0.68,
        "award": 0.62,         # operational, but often a public/distribution hub
        "plan": 0.42,
        "search": 0.30,        # looking for a place: not a site yet
        "unknown": 0.35,
        # aliases so a signal without raw_state.lifecycle still scores sanely
        "operational": 0.88,
        "permit_granted": 0.78,
        "permit_requested": 0.42,
        "under_construction": 0.68,
        "rumoured": 0.42,
        "nomination": 0.30,
    },
    "permit": {
        "operational": 0.80,
        "permit_granted": 0.78,
        "under_construction": 0.70,
        "permit_requested": 0.52,
        "rumoured": 0.35,
        "unknown": 0.40,
    },
    "osm": {"*": 0.58},        # private/customers/hgv tagged charge point in OSM
    "subsidy": {"*": 0.72},    # named beneficiary of a charging subsidy
    # Operator's own fleet-management record: exact coordinates, per-connector
    # power and an explicit truckCompatibility rating. Stronger than a news
    # mention because it is the operator's own published data, but a "planned"
    # site is still only a plan.
    "depotcharge": {"operational": 0.90, "planned": 0.52, "unknown": 0.45},
}

# Counter-evidence: the site is part of the public charge network, i.e. not our product.
COUNTER = {"ndw": 0.80}

# A public hub is not a depot: same treatment as a public charge point.
PUBLIC_ACCESS_PENALTY = 0.80

# A "laadplein" with no company attached is a shared/public plaza, not a private depot.
# Boss's product is depot chargers on company yards, so these become leads, not finds.
ANON_PLAZA_PENALTY = 0.55
PLAZA_RE = re.compile(
    r"laadplein|laadplek|laadhub|oplaadplein|charging\s+plaza|laadstation\s+openbaar",
    re.I,
)

TIER_A = 0.85
TIER_B = 0.60
TIER_C = 0.30

MAX_EXTRA_ITEMS_PER_CHANNEL = 2
EXTRA_ITEM_BONUS = 0.04


def _lifecycle_of(sig: dict) -> str:
    raw = sig.get("raw_state") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    return raw.get("lifecycle") or ""


def _weight_for(sig: dict) -> float:
    kind = sig["kind"]
    table = WEIGHTS.get(kind)
    if not table:
        return 0.0
    if "*" in table:
        return table["*"]
    state = _lifecycle_of(sig) or (sig.get("status_hint") or "unknown")
    return table.get(state, table.get("unknown", 0.0))


def score_evidence(items: list[dict]) -> dict:
    """Stack evidence items for one premises into {score, tier, reasons, channels}.

    items: dicts with at least kind, weight-relevant status, doc_url, observed_at.
    """
    by_channel: dict[str, list[tuple[float, dict]]] = {}
    counter_items: list[dict] = []
    for it in items:
        if it["kind"] in COUNTER:
            counter_items.append(it)
            continue
        w = _weight_for(it)
        if w <= 0:
            continue
        by_channel.setdefault(it["kind"], []).append((w, it))

    reasons: list[str] = []
    channels: dict[str, float] = {}
    product = 1.0
    for kind, entries in by_channel.items():
        entries.sort(key=lambda pair: pair[0], reverse=True)
        top = entries[0][0]
        bonus = min(len(entries) - 1, MAX_EXTRA_ITEMS_PER_CHANNEL) * EXTRA_ITEM_BONUS
        effective = min(top + bonus, 0.97)
        channels[kind] = round(effective, 3)
        product *= 1.0 - effective
        src = entries[0][1]
        reasons.append(
            f"{kind}: {src.get('status_hint') or _lifecycle_of(src) or 'signal'} "
            f"({effective:.2f}) — {src.get('operator') or ''} "
            f"{src.get('city') or ''}".strip()
        )

    score = 1.0 - product

    if counter_items:
        factor = 1.0 - COUNTER["ndw"]
        score *= factor
        reasons.append(
            f"counter: {len(counter_items)} public charge-point signal(s) on the same "
            f"premises ({factor:.2f}x)"
        )

    if any((it.get("access") or "") == "public" for it in items):
        score *= 1.0 - PUBLIC_ACCESS_PENALTY
        reasons.append("counter: public laadplein/hub — not a private depot site")

    if not any(it.get("operator") for it in items):
        blob = " ".join(
            f"{it.get('name') or ''} {it.get('text_excerpt') or ''}" for it in items
        )
        if PLAZA_RE.search(blob):
            score *= ANON_PLAZA_PENALTY
            reasons.append(
                "counter: anoniem laadplein zonder bedrijfsnaam — vermoedelijk openbaar"
            )

    located = any((it.get("postcode") or it.get("address")) for it in items)
    if located:
        score = min(score + 0.04, 0.96)

    # An anonymous premises is a place, not a company. It cannot be a verified
    # depot site (tier A/B) — a permit with only a street address is a lead.
    named = any((it.get("operator") or "").strip() for it in items)
    if not named:
        score = min(score, 0.55)
        reasons.append(
            "no_company_name: geen bedrijfsnaam in het bewijs — alleen locatie bekend"
        )

    # A single crowd-sourced OSM node is a lead to verify, not a probability.
    # OSM records private car chargers with the same tags, so unless the evidence
    # is explicitly truck-rated it must not reach tier B.
    sources = {it.get("source") for it in items}
    if sources == {"osm"} and not any(it.get("hgv_capable") for it in items):
        score = min(score, 0.55)
        reasons.append(
            "osm_only: alleen OSM-bewijs zonder HGV/MCS — lead, geen bevestiging"
        )

    if score >= TIER_A and located:
        tier = "A"
    elif score >= TIER_B:
        tier = "B"
    elif score >= TIER_C:
        tier = "C"
    else:
        tier = None

    max_capable = any(it.get("hgv_capable") for it in items)
    if tier and not max_capable:
        # A depot lead does not have to be truck-rated; say so instead of capping.
        reasons.append("note: no HGV/truck wording found — may be a van or company-car site")

    return {
        "score": round(score, 3),
        "tier": tier,
        "reasons": reasons,
        "channels": channels,
        "located": located,
    }


PREMISES_SQL = """
SELECT signal_id, source, kind, name, operator, address, postcode, city, power_kw,
       n_points, hgv_capable, access, status_hint, doc_url, observed_at, raw_state,
       text_excerpt
FROM signals
WHERE last_seen >= now() - interval '730 days'
  AND (operator IS NOT NULL OR postcode IS NOT NULL OR address IS NOT NULL)
  AND kind IN ('news', 'permit', 'osm', 'ndw', 'subsidy', 'depotcharge')
"""


def _key(sig: dict) -> str:
    company = nx.normalize_company(sig.get("operator") or "")
    place = (sig.get("postcode") or sig.get("city") or "").lower()
    if not company:
        # A granted permit names an address far more often than an applicant.
        site = " ".join(
            part for part in (sig.get("address"), sig.get("postcode"), sig.get("city")) if part
        ).lower()
        company = f"site:{site}" if site else ""
    return company, place


def _label(items: list[dict]) -> str | None:
    """Human label for a premises without a named applicant."""
    for it in items:
        if it.get("operator"):
            return it["operator"]
    for it in items:
        where = " ".join(
            part for part in (it.get("address"), it.get("postcode"), it.get("city")) if part
        ).strip()
        if where:
            return f"Laadplein {where}"
    return None


def build(store: Store, settings: Settings) -> dict:
    with store.conn.cursor() as cur:
        cur.execute(PREMISES_SQL)
        rows = cur.fetchall()

    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        raw = row.get("raw_state") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        if raw.get("country") == "BE":
            continue          # scope: the Netherlands
        company, place = _key(row)
        if not company:
            continue
        groups.setdefault((company, place), []).append(dict(row))

    results = []
    for (company, place), items in groups.items():
        verdict = score_evidence(items)
        if not verdict["tier"]:
            continue
        located_item = next((i for i in items if i.get("postcode") or i.get("address")), None)
        first = min((i["observed_at"] for i in items if i.get("observed_at")),
                    default=None)
        last = max((i["observed_at"] for i in items if i.get("observed_at")),
                   default=None)
        results.append({
            "company_key": company,
            "operator": _label(items),
            "city": (located_item or items[0]).get("city"),
            "postcode": (located_item or {}).get("postcode"),
            "address": (located_item or {}).get("address"),
            "tier": verdict["tier"],
            "score": verdict["score"],
            "channels": verdict["channels"],
            "reasons": verdict["reasons"],
            "evidence": [
                {"kind": i["kind"], "source": i["source"], "url": i.get("doc_url"),
                 "observed_at": str(i.get("observed_at") or ""),
                 "status": i.get("status_hint"), "power_kw": float(i["power_kw"])
                 if i.get("power_kw") is not None else None,
                 "n_points": i.get("n_points"),
                 "excerpt": (i.get("text_excerpt") or "")[:400]}
                for i in items
            ],
            "power_kw": max((float(i["power_kw"]) for i in items
                             if i.get("power_kw") is not None), default=None),
            "n_points": max((i["n_points"] for i in items if i.get("n_points")),
                            default=None),
            "hgv_capable": any(i.get("hgv_capable") for i in items),
            "first_seen_at": first,
            "last_seen_at": last,
        })

    results.sort(key=lambda r: (r["tier"], -r["score"]))
    return {"premises": results, "counts": _counts(results)}


def _counts(results: list[dict]) -> dict:
    out = {"A": 0, "B": 0, "C": 0}
    for r in results:
        out[r["tier"]] = out.get(r["tier"], 0) + 1
    out["total"] = len(results)
    return out


def run(store: Store, settings: Settings) -> dict:
    run_id = store.start_run("classify", "premises")
    try:
        result = build(store, settings)
        with store.tx() as cur:
            cur.execute("DELETE FROM premises_scores")
            for row in result["premises"]:
                cur.execute(
                    """INSERT INTO premises_scores(company_key, place_key, operator,
                        city, postcode, address, tier, score, channels, reasons,
                        evidence, power_kw, n_points, hgv_capable, first_seen_at,
                        last_seen_at)
                       VALUES (%(company_key)s, %(place_key)s, %(operator)s, %(city)s,
                        %(postcode)s,
                        %(address)s, %(tier)s, %(score)s, %(channels)s, %(reasons)s,
                        %(evidence)s, %(power_kw)s, %(n_points)s, %(hgv_capable)s,
                        %(first_seen_at)s, %(last_seen_at)s)""",
                    {
                        **row,
                        "place_key": (row.get("postcode") or row.get("city") or "").lower(),
                        "channels": json.dumps(row["channels"]),
                        "reasons": json.dumps(row["reasons"]),
                        "evidence": json.dumps(row["evidence"]),
                    },
                )
        store.finish_run(run_id, status="ok",
                         records_in=sum(len(r["evidence"]) for r in result["premises"]),
                         records_out=len(result["premises"]),
                         note=as_json(result["counts"]))
        return result
    except Exception as exc:
        store.finish_run(run_id, status="failed", error=str(exc))
        raise
