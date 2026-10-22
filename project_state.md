# project_state.md — depot-laadmonitor

_Last updated: 2026-10-22 (dev CT141, 192.168.178.20, /root/projects/depot-laadmonitor)_

## Purpose

Monitor **private depot/company charging locations** in the Netherlands — the chargers
behind a company gate, not public charge points. No public register exists for these, so
the system infers them from indirect evidence at the level of a **business premises** and
outputs a tier + reasons + source URLs. Methodology: `DETECTION.md`.

## Where things stand

| piece | state |
|---|---|
| code | 17 modules + 4 migrations; 37 unit tests green (`pytest -q`) |
| database | PostGIS 17-3.5 in docker `depotmon-db`, host port **5433**, db/user `depotmon` |
| migrations | 001_init, 002_*, 003_private_detection, 004_raw_payloads_default — all applied |
| news ingest | **live**: 3 publisher feeds, 120 items → 8 keyword hits → 7 kept |
| permit ingest | SRU 2.0 Officiële Bekendmakingen, 30-day lookback, running |
| scoring | `depotmon score` → 3 private premises (see below) |
| API / report / validation | not yet run |
| git | not yet initialised (this commit is the first) |

## Live result (news channel only, 2026-10-22)

```
[B] 0.92  Albert Heijn (Zaandam)        3600 kW  2 evidence
[B] 0.88  Hartog & Bikker (Zoeterwoude)  400 kW  1 evidence
[C] 0.30  Oegema (Apeldoorn)             100 kW  1 evidence (searching, not built)
```

Correctly excluded: **Milence** (public laadhub, Belgium) and **Fastned** (public truck
lane) — treated as public counter-evidence, and out-of-scope countries dropped.

## Decisions

1. **No register exists** — RVO publishes no per-beneficiary SPRILA/SPULA award list
   (74 021-row projectendatabase contains none); NDW/OCM/OSM cover public points only.
   Design is therefore inference + verification, not lookup.
2. **Unit of analysis = premises**, keyed `(company_key, place_key)` in `premises_scores`;
   migration 003 + `private_depots` read-model view.
3. **Evidence stacking with noisy-OR**, correlated items from one channel capped
   (+0.04, max 2), so six articles about the same site never fake certainty.
4. **Public is counter-evidence**: NDW presence ×0.20; a public-hub/CPO claim ×0.20.
5. **Tiers**: A ≥0.85 + located (self-declared operational or granted permit + address);
   B ≥0.60 (plan/construction/award/nomination, located to city); C ≥0.30 (lead list).
   No HGV evidence → capped at C.
6. **Scope**: Netherlands only; `raw_state.country` from Belgian cues drops the record.
7. **Publisher RSS only** (ttm.nl, nt.nl, automotive-online.nl) — no aggregators
   (Google News RSS licence excludes non-personal use).

## Bugs found and fixed during the first live run

| bug | fix |
|---|---|
| `raw_payloads.fetched_at` NOT NULL without default → every archived fetch failed | migration 004 |
| `Store.tx()` committed without rollback → one failure poisoned the whole run with a misleading error | try/except + `conn.rollback()` |
| psycopg `AmbiguousParameter` on NULL lon/lat reused in a CASE | explicit `::double precision` casts |
| `PRIMARY KEY (company_key, COALESCE(...))` — expressions are illegal in a PK | `place_key` column |
| address extractor invented "Bikker heeft twee elektrische eActros 600" from prose | `extract_street_address` (suffix-anchored, conservative) |
| "3.600 kW" parsed as 3.6 | Dutch thousands-separator handling |
| equal content hash skipped field refresh → parser fixes never reached existing rows | refresh fields on unchanged hash too |
| award **nomination** headlines scored as operational | `nomination` lifecycle at 0.30 |

## Next actions

1. Finish permit ingest → rescore; permits give the located evidence that lifts a site to
   tier A (news alone rarely carries a postcode).
2. Geocode city → PDOK/BAG address where possible (`resolve/geocode.py`), then attach
   120 m evidence clustering (`resolve/sites.py`).
3. Report + API (`depotmon report`, `depotmon api` on :8093), MapLibre QA in the browser.
4. Validation: 30-site stratified sample (`validation/validate.py`) — precision per tier.
5. Aerial verification stays **flagged off** until ≥50 hand-labelled sites exist.
6. Systemd timers (daily ingest / weekly report) — only after a clean manual week.

## Ops notes

- Run from `/root/projects/depot-laadmonitor` with
  `DATABASE_URL=postgresql://depotmon:depotmon@localhost:5433/depotmon .venv/bin/python -m depotmon.cli …`
- DB was started with a bare `docker run` (no volume) — **data is ephemeral** until the
  compose volume is re-attached; re-ingest is idempotent, so nothing is lost conceptually.
- Disk on CT141 is tight (~6 G free); prune docker build cache before any image build.
