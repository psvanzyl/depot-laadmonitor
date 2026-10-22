# DETECTION.md — how private depot charging is inferred (and why no register exists)

*Status: methodology + live-probed source inventory, 2026-10-22. Applies to the
`depot-laadmonitor` pipeline (`ingest/news.py`, `ingest/permits.py`,
`classify/premises.py`).*

## 1. The premise we have to design around

Depot charging at a business premises is **not a licensed or reportable activity** in
the Netherlands. A company that puts 400 kW chargers behind its own gate:

- needs no charging-specific permit (a private installation on own terrain is covered by
  the regular building/environment rules, and only *sometimes* triggers one);
- gets no entry in any public charge-point register — NDW, OCM, OSM, laadpalen.nl all
  cover **publicly accessible** points only, which is precisely what a depot is not;
- does not have to report kW, point count or commissioning date to anyone.

So a "national dataset of private depot chargers" does not exist and cannot be
constructed by scraping one. Probing confirms it:

| candidate source | probe result (2026-10-22) | verdict |
|---|---|---|
| RVO `projecten.rvo.nl` export | 74 021 rows; **no** SPRILA / SPULA / AanZET rows | no charging register |
| SPRILA (private charging subsidy, up to €350k) | scheme live, **no per-beneficiary award list published** | counts only |
| DSO open data (Enexis, Liander, Stedin) | routed to `data.partnersinenergie.nl` = paid data products; open = anonymised consumption, congestion forecasts | not site-level |
| NDW / OCP / OSM charging data | public access points only | counter-evidence |
| PDOK aerial (25 cm) | reachable, but no labelled sample to train/validate | flagged off |

## 2. Therefore: infer per premises, then verify

The unit of analysis is a **business premises** (operator + address), not a charge point.
Evidence is stacked from channels that are *independently* motivated to publish, and the
output is a **tier with reasons and source URLs** — never a bare "this site exists".

### Channel 1 — Trade press (primary discovery channel)

Sector media publish company announcements: "X neemt twee e-trucks én een eigen laadplein
in gebruik". These name operator, place, power, point count and lifecycle months before
anything else is visible. Probed live on `ttm.nl/feed` (100 items): 7 keyword hits in one
snapshot, including:

- Hartog & Bikker — own laadplein, Zoeterwoude, 400 kW + MW upgrade, operational
- Oegema — Apeldoorn, 100 kW / 125 A searched, not yet a site
- Albert Heijn DC Zaandam — 17 points, 3 600 kW, operational

Implementation: `src/depotmon/ingest/news.py` — publisher-owned RSS only (no
aggregators), one fetch per article, archived + cited. Extraction in
`news_extract.py`: operator from the headline predicate, place from "in &lt;Place&gt;",
kW (Dutch thousands separator handled: "3.600 kW" = 3600), point count, lifecycle
(opening / construction / plan / search / award), private-terrain claim.

### Channel 2 — Permits (legal footprint)

Officiële Bekendmakingen SRU 2.0 hits for the relevant vocabulary (live counts):
`laadplein` 574 · `private laadinfrastructuur` 1 820 · `laadinfrastructuur bedrijf eigen
terrein` 2 232 · `middenspanningsaansluiting` 23 · `snelladen bedrijfsterrein` 13.
Where a permit *is* triggered (transformer/MSA, brandveiligheid PGS 37-3, parking layout,
environmental melding) the notice names the applicant and the address — the strongest
located evidence. Where no permit is needed, this channel is silent: it is precision-
oriented, not recall-oriented.

### Channel 3 — Counter-evidence (public charge network)

NDW (80 788 locations, 1 051 ≥150 kW), OCM and OSM public chargers are used **only** to
push a premises *down*: if the site is in the public network, it is a different product
(a public hub, not a private depot). Public-presence multiplies the score by 0.20.

### Channel 4 — Grid assets (optional, paid)

Connection capacity is the physical bottleneck for depot charging, but DSO data is
commercial (`Energie Dataportaal`) and open datasets are anonymised/area-level. Kept as a
documented gap: if boss buys the dataportaal product, `power_kw` can be corroborated per
premises. Not required for the model to work.

## 3. Scoring model (`classify/premises.py`)

Per channel take the strongest item, then a small bonus for additional items
(+0.04, max 2) — correlated announcements must not add up linearly. Combine with noisy-OR:

```
p = 1 - Π (1 - w_channel)
```

Weights (priors, chosen to be conservative):

| channel | state | w |
|---|---|---|
| news | opening (in gebruik genomen) | 0.88 |
| news | expansion | 0.82 |
| news | construction | 0.68 |
| news | award | 0.62 |
| news | plan | 0.42 |
| news | search ("zoekt laadplek") | 0.30 |
| permit | granted / operational | 0.78–0.80 |
| permit | under construction | 0.70 |
| permit | requested | 0.52 |
| osm | private / customers / hgv | 0.58 |
| subsidy | named beneficiary (when a register exists) | 0.72 |
| **ndw** | **public charge point present** | **×0.20** |

Then:
- +0.04 if a postcode/address is known, ceiling 0.96;
- **tier A** ≥0.85 *and* located — verified lead: self-declared operational site or
  granted permit, with an address a human can check;
- **tier B** ≥0.60 — probable: plan/construction/award or permit, located;
- **tier C** ≥0.30 — candidate: named site without a verifiable address, or thin evidence;
- no HGV evidence → capped at C (we are not monitoring passenger-car depots).

Persisted to `premises_scores` (migration 003) with `reasons` and the evidence array
(source URL + excerpt + observed date per item). `private_depots` view exposes A/B only.

## 4. Honest precision / recall statement

- **Recall is bounded by announcement behaviour.** Companies that neither publish nor
  need a permit are invisible to every open channel — expect roughly a third to two thirds
  of real private HDV sites to surface, with a bias towards larger, award-seeking,
  ZE-zone-affected operators.
- **Precision is highest at tier A** because self-declaration ("we took our own laadplein
  into use") is a direct claim, and permits come from the municipality.
- **Tier C is a lead list, not a finding.** It exists to be worked by a human/telephone
  or by imagery, not to be published as fact.
- The model is a ranking device: every row must keep its URLs, or it becomes an
  unfalsifiable claim.

## 5. Verification loop (what turns a lead into a fact)

1. Tier A/B rows → check the address in PDOK/BAG, then the publisher page (already fetched
   and archived).
2. Aerial tile from PDOK 25 cm at the premises → look for charging cabinets / canopies /
   transformer kiosk; mark `aerial_verified`.
3. Optional: one phone call or site visit per tier-A row per quarter (the only way to get
   kW and point count for silent sites).
4. Aerial-only detection stays **flagged off** until ≥50 hand-labelled sites exist; without
   them any CV claim would be unfounded.

## 6. Legal / ethical guardrails

- Public, publisher-owned sources only; one request per article, rate-limited, robots
  respected; every item cites its publisher URL.
- No login-walled, no CAPTCHA-bypassed, no aggregator-licensed feeds (Google News RSS
  explicitly forbids non-personal use — not used).
- Company-level data only. No named individuals, no personal data joins; 4-digit postcode
  at most in aggregates.
- Imagery use: PDOK CC-BY (attribution), never for inferences about persons.

## 7. What would materially improve the model

1. **A SPRILA award list** (open-data request to RVO/IenW) — would convert a whole channel
   from zero to named beneficiaries.
2. **DSO connection-capacity data** (ACM pressure / dataportaal licence) — physical
   corroboration per premises instead of priors.
3. **A labelled aerial sample (≥50 sites)** — unlocks visual detection of silent sites,
   the only scalable route to the unannounced tail.
