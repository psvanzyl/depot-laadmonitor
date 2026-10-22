"""Validation: spot-check 30 sites against ground truth and report precision.

Two modes:

  python validation/validate.py sample --n 30
      Draws a reproducible random sample, prints an evidence sheet per site and writes
      validation/sample_30.csv with empty `truth_status` / `notes` columns.

  python validation/validate.py score
      Reads validation/sample_30.csv after a human filled in `truth_status`, and reports
      precision of the status classification (overall and per status).

The ground truth must come from operator websites / press / the permit itself. This script
deliberately does *not* guess it: an automated check cannot validate a classification.
It does, however, run mechanical consistency checks that catch the most common failure mode
(a site whose evidence does not support its status at all).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://depotmon:depotmon@localhost:5432/depotmon"
)
VALIDATION_DIR = Path(__file__).parent
SAMPLE_CSV = VALIDATION_DIR / "sample_30.csv"
STATUSES = [
    "rumoured", "permit_requested", "permit_granted", "grid_connection_signalled",
    "under_construction", "operational", "unknown",
]


def connect():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)


def sample(n: int, seed: int) -> None:
    with connect() as c, c.cursor() as cur:
        cur.execute(
            """SELECT site_id, name, operator, address, city, province, municipality,
                      status, confidence, power_kw_announced, power_kw_operational,
                      n_points, hgv_capable, access
               FROM sites_wgs84
               WHERE last_seen >= now() - interval '400 days'
               ORDER BY site_id"""
        )
        sites = list(cur.fetchall())
    if not sites:
        sys.exit("no sites in database — run the pipeline first")

    rng = random.Random(seed)
    # stratify: at least a few from each non-empty status so precision is measurable per class
    by_status: dict[str, list[dict]] = {}
    for s in sites:
        by_status.setdefault(s["status"], []).append(s)
    picks: list[dict] = []
    for status, bucket in by_status.items():
        picks.append(rng.choice(bucket))
    remaining = [s for s in sites if s not in picks]
    rng.shuffle(remaining)
    picks += remaining[: max(0, n - len(picks))]
    picks = picks[:n]

    rows = []
    for s in picks:
        with connect() as c, c.cursor() as cur:
            cur.execute(
                """SELECT source, url, observed_at, excerpt, confidence
                   FROM evidence WHERE site_id=%s ORDER BY confidence DESC""",
                (s["site_id"],),
            )
            ev = list(cur.fetchall())
        sources = sorted({e["source"] for e in ev})
        checks = {
            "has_address": bool(s["address"]),
            "has_power": bool(s["power_kw_announced"] or s["power_kw_operational"]),
            "multi_source": len(sources) > 1,
            "hgv_signal": bool(s["hgv_capable"]),
        }
        supporting = sum(checks.values())
        rows.append(
            {
                "site_id": str(s["site_id"]),
                "name": s["name"],
                "operator": s["operator"],
                "address": s["address"],
                "city": s["city"],
                "province": s["province"],
                "status": s["status"],
                "confidence": s["confidence"],
                "power_kw_announced": s["power_kw_announced"],
                "power_kw_operational": s["power_kw_operational"],
                "n_points": s["n_points"],
                "access": s["access"],
                "sources": ";".join(sources),
                "evidence_urls": ";".join(sorted({e["url"] or "" for e in ev})[:5]),
                "first_excerpt": (ev[0]["excerpt"] if ev else "")[:300],
                "mech_checks_passed": supporting,
                "truth_status": "",
                "notes": "",
            }
        )

    VALIDATION_DIR.mkdir(exist_ok=True)
    with SAMPLE_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {SAMPLE_CSV} ({len(rows)} sites, seed={seed})")
    print("\nEvidence sheet (verify each against the operator site / press / permit):")
    for r in rows:
        print(
            f"\n[{r['site_id'][:8]}] {r['name']}\n"
            f"  operator : {r['operator']}\n"
            f"  address  : {r['address']} {r['city']} ({r['province']})\n"
            f"  claim    : {r['status']} (confidence {r['confidence']}, "
            f"{r['power_kw_announced'] or r['power_kw_operational'] or '?'} kW, "
            f"{r['n_points'] or '?'} points, access={r['access']})\n"
            f"  sources  : {r['sources']} (mech checks {r['mech_checks_passed']}/4)\n"
            f"  url      : {r['evidence_urls'].split(';')[0] if r['evidence_urls'] else '–'}\n"
            f"  excerpt  : {(r['first_excerpt'] or '').strip()[:220]}"
        )
    weak = [r for r in rows if r["mech_checks_passed"] <= 1]
    print(f"\nMechanical warning: {len(weak)}/{len(rows)} sampled sites have ≤1 supporting check.")


def score() -> None:
    if not SAMPLE_CSV.exists():
        sys.exit(f"{SAMPLE_CSV} not found — run `validate.py sample` first")
    with SAMPLE_CSV.open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("truth_status") or "").strip()]
    if not rows:
        sys.exit("no truth_status filled in yet — label the sample first")

    verdicts = []
    for r in rows:
        truth = r["truth_status"].strip()
        claim = r["status"].strip()
        # a site can be "more advanced" than claimed and still be correctly placed
        order = ["rumoured", "permit_requested", "permit_granted",
                 "grid_connection_signalled", "under_construction", "operational"]
        if truth == claim:
            verdict = "exact"
        elif truth in order and claim in order and order.index(claim) > order.index(truth):
            verdict = "overstated"      # we claim a later phase than reality
        elif truth in order and claim in order:
            verdict = "understated"     # we claim an earlier phase (safe)
        else:
            verdict = "wrong"
        verdicts.append((truth, claim, verdict, r.get("site_id")))

    n = len(verdicts)
    exact = sum(1 for v in verdicts if v[2] == "exact")
    overstated = sum(1 for v in verdicts if v[2] == "overstated")
    understated = sum(1 for v in verdicts if v[2] == "understated")
    wrong = sum(1 for v in verdicts if v[2] == "wrong")
    print(f"n={n}")
    print(f"precision (exact)          : {exact / n:.2f}")
    print(f"precision (exact+understated): {(exact + understated) / n:.2f}")
    print(f"overstated                 : {overstated} ({overstated / n:.2f})")
    print(f"wrong label                : {wrong} ({wrong / n:.2f})")
    print("\nMismatches:")
    for truth, claim, verdict, sid in verdicts:
        if verdict != "exact":
            print(f"  {str(sid)[:8]}  claim={claim:<26} truth={truth:<26} {verdict}")
    out = VALIDATION_DIR / "score.json"
    out.write_text(json.dumps(
        {"n": n, "exact": exact, "understated": understated, "overstated": overstated,
         "wrong": wrong, "precision_exact": round(exact / n, 3),
         "precision_exact_or_understated": round((exact + understated) / n, 3)},
        indent=2,
    ))
    print(f"\nWrote {out}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["sample", "score"])
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--seed", type=int, default=20261022)
    args = p.parse_args()
    sample(args.n, args.seed) if args.mode == "sample" else score()


if __name__ == "__main__":
    main()
