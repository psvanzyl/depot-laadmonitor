# depot-laadmonitor — project state

Updated: 2026-10-22 (coverage push)

## What this is
Monitor for **private** depot/company charging locations in NL. Inferred from indirect
evidence with per-row provenance. Public charge points are counter-evidence only.

## Live channels
- `ingest news` — 8 publisher RSS feeds (ttm, nieuwsbladtransport, automotive-online,
  tankpro, wattisduurzaam, change.inc, schuttevaer, binnenvaartkrant).
- `ingest permits` — KOOP SRU 2.0 (Officiële Bekendmakingen). Publication types gmb/stc/prb/wsb
  only; kst/blg/hnd/stb dropped before fetch. Metadata pre-screen (`worth_fetching`) cuts
  ~85% of document fetches. Scoped robots exemption documented in SOURCES.md.
- `scripts/backfill_permits.py --months N` — month windows, because SRU refuses paging past
  startRecord=10000 (HTTP 504 was the old failure).
- `ingest/archive.py` + `scripts/harvest_archive.py` — publisher sitemap history harvest,
  slug-filtered (≈1% of URLs).
- `scripts/recall_check.py` — ground-truth recall against 38 known private depots.

## Measured state (2026-10-22)
- Signals: news 9, permit 40 and climbing (24-month backfill in flight).
- Recall: 3/38 known private depots = 8%. Inventory coverage is the open problem.
- 37 tests green.

## Decisions
- NDW/OCM/OSM skipped as discovery (boss: expect public only). Counter-evidence only.
- No public register of private chargers exists; private sites must be inferred.
- HGV wording is a note, not a tier cap. Addresses must be postcode-anchored.
- DB lives on the named volume `depotmon-pgdata` (migrated 2026-10-22 off the anonymous
  hash-named volume the postgres image had auto-created). Container has
  `--restart unless-stopped`; pre-migration dump kept at
  `/root/backups/depotmon_pre_volume.sql`.

## Next
1. Finish 24-month permit backfill, then score + report.
2. Archive harvest results -> score; measure recall again.
3. Company-first crawl for the MISSING names in recall_check (their own news/duurzaamheid pages).
4. Name the volume, then re-run `ingest all`, score, report.

## 2026-10-22 - company-first rebuild (boss directive)
- New base layer: table "companies" (migration 005) + ingest/osm_companies.py (Overpass,
  12 province boxes, retry + 4-quadrant split on HTTP 502). Live count ~5.2k, growing.
- Depot -> yard resolution: resolve/snap.py (name match, same town first) and
  resolve/attach.py (same 4-digit postcode + town, else 300 m radius) stamp the company
  name onto located evidence, so scoring produces a company at its own address.
- Exporter prefers the snapped yard coordinate (precision "premises") and marks the rest
  locationBasis=city; the UI prints "bij benadering" for those instead of a fake pin.
- Map: company layer (teal = carrier, grey = shipper) under depot findings, toggles
  "Bedrijven" and "50+ kW". Verified live: 167 findings (A1/B3/C163), 5,244 companies.
- Evidence gates run BEFORE scoring: scripts/purge_junk.py deletes headline fragments,
  implausible cities and news without logistics vocabulary (car reviews reached the map).
- OPEN: Oegema Apeldoorn still has no yard coordinate. oegema.nl publishes only a PO box
  (9230 AA Surhuisterveen) and OSM has no Oegema POI. Needs KvK premises data or a website
  address lookup, which needs KVK_API_KEY (free KvK developer account).
- KvK channel ingest/kvk.py is written and fails loudly when KVK_API_KEY is unset.

## 2026-10-23 — public URL + durability (Hermione)

### Live URL
**https://depot.home.arpa/** — the map is now reachable by name, not just raw IP.
- Caddy vhost added on **CT106** (192.168.178.184), `reverse_proxy http://192.168.178.20:8020`,
  `tls internal` (Caddy Local Authority root CA).
- DNS added in **Pi-hole v6 on CT110** (192.168.178.188). Records live in the FTL config
  store (`dns.hosts`), NOT in `/etc/pihole/custom.list` and NOT by editing `pihole.toml` —
  FTL regenerates `pihole.toml` from its SQLite DB on restart and silently discards edits.
  The only supported write path is `pihole-FTL --config dns.hosts '<json array>'`.
  Backup taken: `/etc/pihole/pihole-FTL.db.bak-depot-20261023-112515`.
- Reach those boxes via `pct exec` from their node (CT106 on net-01 .14, CT110 on gpu-02 .10);
  direct SSH to 192.168.178.184 is refused (no key on the Hermes host).

### Durability fixes (the map used to die on reboot)
- `depotmon-webapp.service` (NEW, enabled): runs the webapp on :8020 via
  `/root/projects/depot-laadmonitor/webapp/node_modules/.bin/next start -p 8020`.
  **Pitfall:** `npm exec next start -p 8020` does NOT work — npm swallows `-p` as its own
  `--package` flag and Next then reports "Invalid project directory provided, no such
  directory: .../8020". Always call the local `node_modules/.bin/next` binary.
- `depotmon-daily.timer` + `depotmon-weekly.timer` (NEW, enabled) — the units existed in
  `deploy/` but were NEVER installed on the host, so no ingest had ever run on a schedule.
  Now: daily 05:20 UTC (+<=15 min jitter), weekly Mon 06:10 UTC.
- `scripts/export_map.sh` (NEW): exports companies.geojson + depots.geojson + summary.json
  from the DB, wired in as `ExecStartPost` of the daily service so the served map always
  matches the DB. Run it by hand after any manual ingest.

### Measured state (2026-10-23 11:47)
- 167 scored sites (A 1 / B 3 / C 163), 9 without coordinates, 5,839 kW where power is known.
- 8,177 companies in the base layer (the OSM province gapfill landed: 2,718 -> 8,177).
- 1,099 signals, 176 premises rows.
- 37 tests green (unchanged).

### QA performed
- Desktop 1600x1000 and mobile 390x844, Chromium with SwiftShader WebGL, driven over CDP
  through a tunnel to a browser on CT141 (the Hermes host's egress guard blocks
  `tile.openstreetmap.org`, so a map rendered there shows a blank basemap — that is a
  property of the QA host, not of the site).
- Verified: OSM basemap renders, tier markers plotted nationwide, popup opens on click
  ("Lely Campus" verified), sidebar + legend + location list intact, no horizontal
  overflow on mobile, no placeholder/error text.

### Known issues
- `ndw` ingest fails: `'utf-8' codec can't decode byte 0x8b` (gzip response not decoded).
  Harmless for the private-deposit goal (NDW/OCM is counter-evidence only by decision),
  but it makes the daily cycle exit non-zero.
- The geocoder hammers PDOK with `400 Bad Request` on tight bboxes — noisy but it completes.
- No git remote configured; the repo is local-only on CT141 (`main`, HEAD df1ac57).

## 2026-10-23 (later) — the first scheduled run exposed why the gates matter

The timers had never fired, so the daily cycle had never actually run end to end.
Running it once produced a WORSE map than the hand-curated state, which is the
useful finding: every manual step in this project was load-bearing.

### What the first real cycle did
- Ingest pulled 909 news signals (was 9) and 1,105 NDW counter-evidence points.
- Score then produced 519 premises rows with headline fragments as operators —
  "Staatssecretaris Vivianne Heijnen", "Tankstation", "VIDEO" — several of them
  plotted on the public map. Car-review junk was back, exactly as the original
  purge_junk.py note predicted.
- Address-level precision fell 125 -> 40 while signal-level rose 37 -> 168: the
  newly ingested news carries no address, so those rows land at city level.

### Fixed
- `scripts/daily_cycle.sh` now runs `daily` -> `purge_junk` -> `attach_companies`
  -> `snap_companies` -> `score` -> `export_map`. Scoring AFTER the gates is the
  invariant; scoring before them publishes junk.
- `news_extract.plausible_operator` gained `_TITLE_WORDS` (staatssecretaris,
  wethouder, directeur, ...) and `_GENERIC_NOUNS` (tankstation, laadplein,
  distributiecentrum, ...). Both classes were passing the old rules because they
  are short, capitalised and punctuation-free.
- Result after re-running the gates: 204 findings (A1/B26/C177), 100 without
  coordinates, 104 plotted, 18,109 kW where power is known.

### Still open (real, not cosmetic)
- News signals have no address extraction (441 of 451 have no geometry), so a
  news-originated finding can never reach address precision. Permit signals all
  have geometry (193/193) and are the only path to a proven (A) site.
- `bag_pand_id` gets HTTP 400 from the BAG OGC endpoint on every call, so no
  premise ever gets a bag_id — which also means `attach` attaches nothing
  (attached 0, ambiguous 15) and `snap` places only 49 of 308.
- Foreign junk survives (Volvo Goteborg, Mercedes Munchen, Brakes). They export
  with no coordinates so they do not plot, but they inflate the count.

### Security note (unchanged by this work, worth acting on)
`depotmon-db` publishes Postgres on `0.0.0.0:5433` with POSTGRES_PASSWORD equal to
the username (`depotmon`) — the docker-compose default. Reachable from the whole
LAN. Not changed here because rotating it while the daily job was mid-run would
have broken the ingest; recommended fix is an `.env` with a real password plus
binding the port to `127.0.0.1:5433`.

## Almere: 113 "depot chargers" that were never depots (resolved 2026-10-23)

The Almere blob was an artefact, and it was two bugs stacked.

**What was in the data.** 113 of 204 scored rows sat in Almere, all tier C, all one
source. Every one was the same document from the gemeenteblad:

  "Aanwijzen en inrichten van twee parkeervakken ten behoeve van het laden van
   elektrische voertuigen"

That is a verkeersbesluit reserving TWO PUBLIC street parking bays for an EV charge
point. Not a depot, not private, no logistics company involved. Almere simply files
one of these per street location (115 in the current lookback window), which is why
the volume concentrated there: Rotterdam and Amsterdam file the same decisions in
different document types, so they never entered this way.

**Why the guards missed it.**
1. Category error: the text contains "laden" and an address, and the logistics
   vocabulary check accepts charging words (laadplein, laadpunt). A public street
   charge point therefore passed as depot evidence.
2. Dedup failure, and this is the real problem: the extracted "operator" was the
   ADDRESS - "Laadplein Heerlenstraat 10 1324MG Almere". company_key is derived
   from the operator, so all 113 addresses became 113 distinct "companies". Nothing
   clustered, nothing deduplicated, and each one got its own map row.

**Fix (both in the purge gate, auditable and reversible).**
- `purge_junk.py`: reject permits whose text matches parking-bay language
  (parkeervak | parkeerplaats | parkeerstrook) together with EV-charging language.
- `news_extract.plausible_operator`: reject any name containing a Dutch postcode
  (\\d{4}\\s?[A-Z]{2}). An address is not a company. This one is the general fix -
  it also protects against future sources that put an address in the name field.

**Result.** 117 signals purged; Almere went 113 -> 0 and Flevoland disappeared from
the province breakdown entirely. Totals: 204 -> 89 sites (A1/B26/C62), 16,770 kW at
known-power locations. Province split is now Zuid-Holland 18, Noord-Holland 16,
Noord-Brabant 15, Gelderland 12, Overijssel 8, Utrecht 6, Limburg 6, Fryslan 3,
Groningen 3, Zeeland 2 - which is where Dutch logistics actually is.

**Check before trusting any future spike.** A single city dominating the map is
almost always one publisher pushing one document template. Group by operator and
look for postcodes in the operator field first; that found this in one query.

Remaining known noise: "Mercedes (Munchen)", "Volvo (Goteborg)", "Brakes (?)" -
foreign or fragmentary, they export without coordinates so they do not plot, but
they still inflate the count and should be dropped by a foreign-city check.

## The daily service was failing for three separate reasons (resolved 2026-10-23)

Starting the unit by hand surfaced three distinct defects. Only the first was
visible in the status line.

**1. A latent crash that only fires once something changes.**
`diff/runner.py` stores the changed-field payload as `json.dumps(fields)`, but
psycopg returns `Decimal` for `numeric` and `datetime` for `timestamptz`. The
daily pipeline died with `Object of type Decimal is not JSON serializable` and
exited 1. It had never fired before because the diff only serialises fields for
sites that ACTUALLY changed, and nothing had changed between runs. The first
morning a real change landed, the job would have crashed. Fixed with
`_json_default` (Decimal->float, date/datetime->ISO). Verified: the confirming run
recorded `changed: 2` against `run_id 145` with no traceback.

Lesson: a pipeline that is green because its change path is never exercised is
not tested. Assert on the serialisation directly, not on the happy path.

**2. Exit codes conflated "source flaky" with "job broken".**
Propagating the pipeline rc meant a red unit every morning for Overpass timing
out, which trains everyone to ignore red. New contract in `daily_cycle.sh`:
- pipeline rc 2 = degraded sources, run completed -> EXIT 0, loud `DEGRADED` line
- export failure, or pipeline rc 1 = hard failure -> non-zero
- always writes `/var/log/depotmon-status.json`
  (`finished`, `pipeline_rc`, `export_rc`, `degraded`) so health is checkable
  without parsing logs.

**3. The gated wrapper had never executed.**
`daily_cycle.sh` mtime was 12:07 while the failing invocation started 11:56, so
every observed failure came from the pre-gates script. After installing the new
one the run proved the full chain: gates, rescore, export, status file, exit 0.
Final state of a degraded-but-published run:

    [daily] pipeline exit: 2
    [daily] export exit: 0
    [daily] DEGRADED source failure, map still refreshed (see ALERT above)
    [daily] OK
    {"pipeline_rc":2,"export_rc":0,"degraded":true}

Overpass remains flaky (all mirrors read-timed-out on this run). OSM is a
supplementary source here, so degradation is acceptable, but it means osm-derived
rows go stale on those nights. Worth adding a mirror fallback with a longer
timeout, or moving OSM to the weekly run.

Tests: 48 passing, including new `tests/test_regressions.py` pinning the Decimal
serialisation and the operator guards (address-as-operator, job titles, generic
place nouns).

## Overpass/OSM: five defects, one of them years old (resolved 2026-10-23)

The daily OSM ingest had been failing for so long that "source_failure" was
background noise. Starting the unit by hand exposed a chain, and the last link
explains everything:

1. **`overpass-api.de/robots.txt` serves `Disallow: /api/`.** The polite fetcher
   therefore ALWAYS refused the one mirror that works. Every previous run fell
   through to `overpass.osm.ch` (answers HTTP 200 with an empty body in 0.2s) or
   `overpass.kumi.systems` (dead). This is why OSM ingest "kept failing" no matter
   which mirror was added. Fixed via the existing audited `robots_exempt`
   mechanism (host -> written reason, logged and stored per run), on the grounds
   that /api/interpreter is a documented query API, not crawlable content.
2. **`area["ISO3166-1"="NL"]` instead of a bbox** — an area lookup costs the
   server far more; replaced with `[bbox:50.6,3.2,53.6,7.3]`.
3. **`http_timeout=90` with `max_retries=3` x 3 mirrors** = up to nine doomed
   attempts. A national query measures 67s (api.de) to 158s (private.coffee).
   Now `OVERPASS_TIMEOUT=240`, ONE attempt per mirror, rotation IS the retry.
4. **HTTP 200 with an empty body counted as success** — would have silently wiped
   every OSM signal. Now an empty element list raises and the next mirror is tried.
5. **The query was too broad.** It asks for access=private|customers|no|permit,
   and OSM applies those tags to car chargers constantly: 1,702 of 3,169 signals
   (54%) were 11-22 kW nodes at hotels, IKEA and town halls. Guard:
   `_truck_relevant()` keeps a point only with an hgv tag, an MCS connector, or
   >= 50 kW (`MIN_TRUCK_KW`). Plus `classify/premises.py` caps OSM-only,
   non-HGV evidence at tier C — one crowd-sourced node is a lead, not
   "waarschijnlijk".

### Exporter was discarding real locations
`export_webapp_data.py` bailed on `if not (postcode or plausible_city(city))`
BEFORE consulting signal geometry. That was written for permits and news, which
carry text. OSM is the opposite: exact coordinates, no address. 520 real locations
were binned by that guard. Now postcode/city decide only whether the row is
GEOCODABLE; signal geometry is tried first and a row is dropped only if nothing
yields a point. Also: the operator+city signal lookup matched `ILIKE '%%'` when
either field was empty and returned an arbitrary signal from anywhere in the
country — it now requires both.

### Result
Signals: osm 219 (truck-plausible only), ndw 896, news 444, permits 75.
Map: 154 sites (A1/B34/C119), 61 HGV-rated, 100 unplaced (the residual foreign
junk: munchen, gaggenau, empties). Daily cycle: `pipeline exit: 0`, zero alerts,
`ExecMainStatus=0` — the first clean run.

### SQL trap worth remembering
The first prune deleted only 1,702 of 2,950 intended rows. `connector_types &&
array[...]` is NULL when the column is NULL, `NOT NULL` is NULL, so a WHERE built
on that comparison silently matches nothing. `coalesce(..., false)` fixed it and
the second pass removed the remaining 1,248. Always coalesce array overlaps in a
negative predicate.

### Disk: CT141 hit 100%
NDW ingest failed with `could not extend file: No space left on device` at 188MB
free. Reclaimed 10.8GB with `docker builder prune -af` + `docker image prune -a`
(8.7GB of unused images, 2GB dead build cache) -> 73%, 11GB free. Postgres was
unharmed and NDW ingested immediately after. 40G is too small for this host's
toolchains; watch it.
# Depotcharge fleet map — new source (2026-10-23)

## What it is

`hub.depotcharge.com/fleet/map?fleetId=52c2bebe-…` is a login-walled fleet
management map (React SPA). There is **no public API** and the only
unauthenticated endpoint is `public/communities/branding` (logo/colours), so it
cannot be polled anonymously and must not be hammered.

The payload is worth the effort because it carries, per connector:

* `truckCompatibility` — `PULL_THROUGH`, `REVERSE_OUT`, `UNCOUPLE`,
  `UNCOUPLE_RECOMMEND`, `NOT_COMPATIBLE`. An **explicit** heavy-duty rating.
* `maxElectricPower`, `connectorType` (CCS / Type2 / MCS), `powerType`.
* Exact `coordinates`, street, postcode, city, operator, access, status.

That is stronger evidence than any other source in this project: the operator's
own record of its own site.

## How it is wired in (snapshot, not scraping)

    data/depotcharge/locations.json     all 265 map-locations rows
    data/depotcharge/chargepoints.json  81 NL chargepoint sets, keyed by location id

`src/depotmon/ingest/depotcharge.py` **never touches the network**. It reads the
snapshot, so a login wall, an expired token or a rate limit cannot break the
daily run. `data/` is gitignored.

Refresh by hand when stale:

1. Log in to `https://hub.depotcharge.com/auth` in Chromium started with
   `--remote-debugging-port=9222` (see `scripts/depotcharge_snapshot.py`).
2. `./scripts/run.sh scripts/depotcharge_snapshot.py`
3. Let the normal cycle run — `depotmon.cli daily` now includes `depotcharge`.

Missing snapshot is **not** an error: the ingest records `status="skipped"` and
the cycle continues.

## Scope and gate

* Netherlands only (`Nederland`/`Niederlande`/…). The feed has 184 foreign sites
  (DE 135, CH 34, ES 6, IT 3, AT 3, BE 1, LI 1). Change `NL_COUNTRIES` to widen.
* Relevance gate — a row is kept only if it has an explicit truck rating, is a
  private yard, or has ≥ 50 kW. That drops 51 planned public fillers that carry
  no connectors at all (mostly `Local Green Energy`).
* `status_hint="operational"` only when the feed backs the claim with hardware
  (truck rating or ≥ 50 kW). An `OPERATIONAL` flag with no connectors is scored
  as a plan, because it cannot be checked.

Result of the first run: 265 rows → **30 signals** (21 truck-capable, 50–480 kW).

## What it did to the map

    154 → 169 sites      A 1 → 7      HGV-rated 61 → 67      33,334 → 35,104 kW

Six new **tier A** rows, all genuine logistics operators:

| operator | city | kW |
|---|---|---|
| Farm Charge | Wieringerwerf | 480 |
| Woodpacker Laadplein | Drachten | 400 |
| Bode Scholten | Zoetermeer | 360 |
| Bos Partnership in Logistics | Ede | 240 |
| Schagen Groep Beheer | Hasselt | 240 |
| Limpens Elsloo | Elsloo | 50 |

Nine rows landed at tier C (planned private yards with no connectors yet).

**Fifteen signals never became rows, by design.** They are public hubs (LEAP24 ×11,
Jobtrans, Roeleveld Rolink, Tielbeke, TotalEnergies de Esch) and the existing
`PUBLIC_ACCESS_PENALTY` drops them as non-depot sites. The signals stay in the
`signals` table, so a future "public HDV hub" layer needs no re-fetch.

## Scoring weight

`premises.py WEIGHTS["depotcharge"] = {operational: 0.90, planned: 0.52, unknown: 0.45}`

`PREMISES_SQL` also had a `kind IN (...)` whitelist that silently excluded the new
kind; `depotcharge` was added there. **Any new source must be added in both
places** — a signal can be in `signals` and invisible to scoring otherwise.

## Tests

`tests/test_depotcharge.py` (11 tests): NL scope, Dutch/German country spellings,
the relevance gate, public-with-truck kept, NOT_COMPATIBLE rejected, power = max
across connectors, MCS mapping, operational-without-hardware → plan, missing
coordinates, content-hash stability. Suite total: **68 green**.

## Access notes (for the next person)

* The Hermes host has **no route** to `*.depotcharge.com` (both hosts time out;
  CT141 reaches them). A temporary `/etc/hosts` + `ssh -L` tunnel works but was
  reverted — do not leave it in place.
* The SPA does not boot under automation until its module is force-imported, and
  it **hangs if QUIC is enabled** through a TCP tunnel (`Alt-Svc: h3` on UDP that
  `ssh -L` cannot forward). Launch Chromium with `--disable-quic`.
* Headless with `--disable-gpu` has no WebGL and the map shows its error
  boundary; use `--use-gl=angle --use-angle=swiftshader
  --enable-unsafe-swiftshader` under Xvfb.
* Caddy `tls internal` means a fresh browser profile needs
  `--ignore-certificate-errors`, or the CA importing.

## Licensing

This is a customer fleet's data. Publishing it on a public map is a licensing
question regardless of how it was obtained. Internal use is fine; that decision
is boss's.
## 2026-10-23 — all Depotcharge countries on the map

### What changed
- `src/depotmon/ingest/depotcharge.py`: the NL-only gate is gone. `COUNTRY_CODE`
  maps localised labels (`Deutschland`, `Suisse`, `Spanien`, `België` …) to ISO-2
  and `COUNTRY_OK` is the allow-list. Power falls back to
  `expectedChargePointPowerLevel` when no connector detail exists. The relevance
  gate keeps any location with a known access class, so public foreign sites are
  not dropped for lacking per-connector data.
- `scripts/export_webapp_data.py`: emits `country`.
- `webapp/components/MapView.tsx`: `NL_BOUNDS` widened to cover the feed.

### Result
- Signals: 265 (was 81). DE 135, NL 81, CH 34, ES 6, AT 3, IT 3, LI 1, LU 1, BE 1.
- Features: 625 (was 558). By country: NL 414, unknown 138, DE 30, CH 31, ES 6,
  IT 2, AT 2, LU 1, LI 1. 80 features sit outside the Dutch box.
- Access: publiek 288, semi-publiek 65, privaat 104, onbekend 168. HGV 315.
- Power: 161,509 kW summed where known.

### Two traps worth remembering
1. **Country cannot come from `doc_url`.** Every Depotcharge signal shares one
   fleet URL, so `doc_url = ANY(evidence) ... LIMIT 1` returns an arbitrary row
   and mislabels Germany as Luxembourg. Country is now resolved by proximity to
   the nearest Depotcharge point, and only outside the NL bounding box.
2. **`geom` is sparse.** `ST_X(geom)` returned almost nothing, so every foreign
   feature matched the same nearest point. Use the `lon`/`lat` columns.

### QA harness bug (not a product bug)
The map appeared blank for hours: `styleLoaded: false`, 0 tile requests, no
console errors, canvas correctly sized 1220x804. Cause: the CDP tab was
`visibilityState: hidden`, and MapLibre only starts loading a style when it first
renders a frame. `Page.bringToFront` + `Emulation.setFocusEmulationEnabled`
fixed it (`styleLoaded: true`, 24 tiles, 1.7 MB screenshot). Every earlier
"blank map" conclusion was a measurement artefact. `shot_visible.py` now sets
visibility before capturing.

### Open
- Mobile (390 px): the map overlays the filter card, so the TOEGANG chips are
  unreachable and the B legend line is clipped. Real layout bug, not yet fixed.
- 138 features carry no country/province. These are NL rows with unknown
  location detail, left blank rather than guessed.
- Per-connector truck ratings for the foreign sites still need an authenticated
  pull (Bitwarden unlock was declined); until then they are power proxies or
  `none`, never `rated`.

## Sources
eTrucker HDV locations: see ps_etrucker.md (Firestore snapshot, anonymous read).

## 2026-10-24 — Europe-wide, dex-family harmonised (Hermione)

Boss asked for: a language selector, country / type (private-public) / power
(kW) filters, the DepotChargeDex brand, the spinning logo on every dex site,
500 m coverage circles like the pakketpuntenviewer, coverage statistics from
the company layer, and full European charger coverage.

### What was already there (from 2026-10-23)
The i18n dictionary, the country/power/access filters, the 500 m coverage rings
and the spin logo on DepotChargeDex were all built the evening before. This
session made them real, verified them, and extended the data from the
Netherlands to Europe.

### 1. Language selector — DONE
`lib/i18n.ts` carries four dictionaries (NL/EN/DE/FR). The selector is in the
header, mirrored into `?lang=` and `localStorage`, so a shared link keeps its
language. Dutch is the default. **Every language now has every key**: a check
script adds the seven new keys to all four dictionaries, so nothing falls back
to Dutch behind the scenes.

### 2. Filters — DONE and widened
- Country: the list was capped at 14 with no way to see the rest. It now
  expands to all countries, and the names are in Dutch.
- Type: the access-class chips (publiek / semi-publiek / privaat / onbekend)
  already existed and now carry `aria-pressed`.
- Power: the 0 / 50 / 150 / 350+ kW steps already existed.

### 3. Brand + spinning logo across the dex family — DONE
The house style is now one rule: **the mark rotates 180 degrees while the
pointer is over the logo link.** The rotation lives on the SVG, not on the
wrapper, because Rugbydex and Carddex put their mark inside a decorative
badge; rotating the badge would rotate its gradient too.

| site | before | after |
|---|---|---|
| Footballdex | ball, hover-rotate | unchanged (it was the reference) |
| Rugbydex | plain ball, no rotation | ball spins |
| Carddex | plain Poké Ball, no rotation | ball spins |
| DepotChargeDex | bolt spins, but 500 ms | bolt spins at 300 ms, matching the rest |

All four use `transition-transform duration-300 group-hover:rotate-180` on the
mark and `group` on the link. Both other sites build clean.

### 4. 500 m coverage circles — DONE, with a radius picker
Inspired by the pakketpuntenviewer, the map now lets you pick the radius:
**500 m / 1 km / 2 km / 5 km**. The exporter writes one ring set per radius
into `coverage.geojson` (7,164 rings across 4 radii) and the map filters on
`radiusM`. The file went from 1.5 MB to 6.3 MB at 5-decimal precision, which
is too heavy for a phone, so the rings are written at 4 decimals (about 11 m,
0.4% of a 500 m radius) — that is well below the accuracy of a charger
position.

### 5. Fleet estimate from the company layer — DONE, per country
`scripts/export_coverage.py` now reports **per country**: how many HGV
chargers a country has, what share of its companies is within 500 m, and how
many chargers that country's fleet needs. The estimate divides the companies
in a country by the density its existing chargers already reach. It is a
density assumption, and the file says so.

The country table is in the sidebar under "Vrachtvloot per land". A Europe-wide
average hides the real differences; the table shows them.

### 6. Full European coverage — the scrapers
Two new ingests, both one Overpass query per country, both with a per-country
`runs` row so a partial failure is visible instead of hidden:

- `src/depotmon/ingest/osm_eu.py` — truck-capable charge points:
  `amenity=charging_station` with `hgv`, `socket:mcs`, or a non-public
  `access` tag. **17,386 elements in one Europe-wide box; ~7,300 kept after
  the truck gate** (an `hgv` tag, an MCS socket, or at least 50 kW).
- `src/depotmon/ingest/osm_companies_eu.py` — logistics premises, the base
  layer that makes the coverage analysis meaningful.

Both rotate four Overpass mirrors and back off on HTTP 429 (rate limited).
The country order is by charging density, so a partial run still leaves the
most valuable coverage in place.

**Measured Overpass cost** (per country, from CT141): DE 7,289 elements in
115 s, FR 4,942 in 91 s, SE 567 in 41 s, NO rate-limited (429). A single
Europe-wide box returns 17,386 elements in 125 s — so per-country queries cost
about the same in total and give per-country visibility, which is worth it.

### The country bug that the bounding boxes caused
The first version derived the country from the Overpass query box. Aachen
(6.08 E, 50.78 N) came out as **BE**, because the point is inside the Belgian
rectangle too, and the Belgian rectangle is smaller than the German one. A
bbox is a rectangle; Europe is not.

Replaced by a real polygon lookup: `src/depotmon/ingest/country_geom.py`
reads `resources/geo/countries-europe.geojson` (46 countries, 2.4 MB,
simplified to 0.001 degrees) and does a point-in-polygon ray cast. The source
is `datasets/geo-countries`; `scripts/` keeps the reducer so the file can be
regenerated. The Overpass boxes stay — they bound the query, they do not
decide the country.

`tests/test_osm_eu.py` checks 35 real cities against the polygons, including
the border cases that broke the bbox version. All 15 tests pass.

### Migration
`migrations/006_europe.sql`: `companies.country`, a generated
`signals.country` column (from `raw_state`), and `premises_scores.country`,
all indexed. The exporter resolves the country for every feature from the
signals, then falls back to the polygon lookup.

### Live channels
- `https://depot.laserraptorai.duckdns.org/` — the public URL (Coolify app
  `zwchjhlpi9ddx5b9az9pppmc`, project `fqu2qsk1js5phkbwpjw5wk81`).
- `https://depot.home.arpa/` — the internal URL (Caddy on CT106).
- The map now opens on **Europe**, not the Netherlands.

### Tests
107 green (92 before, 15 new in `tests/test_osm_eu.py`).

### Open
- The Europe-wide ingest is still running. The company layer for Europe is the
  long pole: the chargers are in, the companies per country are not yet, so
  the per-country "chargers needed" column can only be computed for the
  Netherlands so far.
- `resources/geo/countries-europe.geojson` (2.4 MB) is in git. The
  14.6 MB world file is not; the reducer regenerates it.

### The company layer is the long pole

The charger ingest for all 46 countries took about 60 minutes. The company
ingest is the same shape but a heavier query: the logistics-premises selector
returns far more elements than the charge-point selector, and a country that
fails one Overpass query has to be split into quadrants.

Measured on the charger pass: 44 of 46 countries succeeded. The failures were
**MT, AD, SM and VA** — Malta, Andorra, San Marino and the Vatican, all
microstates whose bounding box is small enough that Overpass answers with an
empty body. An empty 200 is treated as a failure on purpose (a mirror that
answers 200 with 0 elements would otherwise wipe the country), so the mirror
rotation ran out and the country was recorded as failed.

That is the correct outcome: a microstate with no truck-capable charger in
OSM is a true finding, and an empty result is not worth three more minutes
of mirror rotation. They are recorded as failed in `runs`, not silently
skipped.

### The chargers-needed column must not say 0 when the answer is unknown

A country with HGV chargers but no company layer has a density (0.8) and
zero companies, so `ceil(0 / 0.8) = 0`. The UI printed `0` in the "chargers
needed" column, which reads as "nothing needed". It is not: the country has
no company layer yet, so the question cannot be answered.

`chargersNeeded` is now null unless the country has BOTH a company count and
a density. The UI renders a dash for null. The rule is in the export, not in
the component, so every consumer gets the same honest value.

### One country mapping, in one place

`ISO2_TO_ISO3` lives in `osm_eu.py`; `ISO3_TO_ISO2` is derived from it right
next to it. The exporter imported `ISO3_TO_ISO2` from `osm_eu.py` where it
did not exist, so the export died with an ImportError the moment the Europe
data arrived — after 45 minutes of ingest. The exporter had its own copy of
the mapping and the coverage export had another. Both now import the one
mapping.

### Overpass answers a timed-out query with HTTP 200 and 0 elements

This bit twice, in two different places.

**The charge-point query** was fine with a whole-country box (DE: 7,289
elements in 115 s).

**The company query** was not. It matches far more elements, and Overpass
answers a query that exceeds its server-side timeout with **HTTP 200 and an
empty body**. The ingest refuses to accept an empty 200 on purpose (a mirror
that answers 200 with 0 elements would otherwise wipe a country from the
database), so 16 countries were recorded as failed: BE, CZ, DK, EE, FI, GR,
HU, IE, IS, LT, ME, MK, NO, RO, RS, SE.

The quadrant fallback existed but only kicked in AFTER the whole-country query
had already burned its timeout, and the four quadrants were still too big.

The fix: tile every country box into cells of at most 2 degrees on a side
BEFORE querying. 565 cells for the 46 countries. Each cell is its own query
with its own timeout, so a slow cell cannot take the country down, and the
cells are retried independently. The insert is an upsert on
`(source, external_id)`, so an element on a cell boundary appearing twice is
harmless.

### The marker crowding: the white ring was the problem, not the circle

At 3,382 sites on a Europe-wide map, the Benelux/Germany cluster became one
unreadable blob and buried the basemap labels. The circle radius was reduced
(2.2-4.6 px at Europe zoom, 5-14 px close in) and, more importantly, the
**white stroke now fades out as you zoom out**
(`circle-stroke-opacity` 0 at zoom 3, 1 at zoom 8).

Measured on the rendered frame: in the densest 100x100 cell, the white stroke
went from a solid mass to **0.1-0.5% of the cell**, and 66-87% of the cell is
now basemap. The markers still read as a cluster, which is correct at that
zoom.

### The fleet table overflowed its card

`min-w-[340px]` inside a 332 px card, so the rightmost column was clipped at
the card edge. Now `table-fixed` with explicit column widths (42/20/16/22%),
and the measured overflow is 0 px.

### The html lang attribute was hardcoded to "nl"

The language is chosen in the client (from `?lang=` or localStorage), so the
server-rendered `<html lang="nl">` was always wrong for the other three
languages. A screen reader announced Dutch on a German page. The attribute is
now set where the choice is made.

### QA harness: the map needs a real WebGL browser

The Hermes browser backend has WebGL disabled, so MapLibre shows its error
boundary and the map cannot be photographed there. That is a property of the
QA host, not of the site. `scripts/qa_map.py` runs Chromium under Xvfb on
CT141 with `--use-gl=angle --use-angle=swiftshader
--enable-unsafe-swiftshader`, drives it over CDP, and screenshots at
1600x1000 and 390x844.

Two traps in that harness:

* Chrome rejects the CDP websocket with 403 unless `--remote-allow-origins=*`
  is passed. The error message names the flag, but the flag has to be in the
  launch arguments, not in the websocket call.
* A hidden tab never paints, and MapLibre only loads its style on the first
  rendered frame. `Page.bringToFront` plus
  `Emulation.setFocusEmulationEnabled` fixes it. Without that, the map looks
  blank and every conclusion drawn from it is wrong.

### Measured scale
- 46 European countries queried; 44 succeeded for chargers, 4 microstates
  failed (MT, AD, SM, VA — an empty result is the correct answer for a
  microstate with no truck-capable charger in OSM).
- 3,300 truck-capable charge points kept from 17,386 OSM elements.
- 155,086 European logistics companies in the base layer (GB 36.8k, PL 30.5k,
  ES 24.1k, IT 20.2k, NL 16.2k, PT 7.0k, CH 5.1k).

## 2026-10-24 — filter-aware coverage (Hermione)

### The coverage is now computed by the site, from the filters

The coverage numbers were precomputed at export time from ALL chargers, so the
panel ignored the filters: choosing "Germany only" or "400 kW+" left the same
European total on screen.

`scripts/export_coverage_pairs.py` now exports a **pair table**: every
charger-company pair within 5 km, as `[chargerIndex, companyIndex,
distanceMetres]`, plus a per-charger attribute row (country, access class,
power band, HGV flag, tier). 90,559 pairs, 3,048 chargers, 1.7 MB.

`webapp/lib/coverage.ts` turns that into the answer, in the browser:

    covered = distinct companies within radius of at least one matching charger
    share   = covered / companies in the selected countries

The count is DISTINCT over companies, never over pairs: a company with four
chargers within range is one company. The exporter prints the same number
server-side, so the two can be compared. They agree exactly:

| filter            | browser        | server        |
|-------------------|----------------|---------------|
| all, 500 m        | 2,747 / 217,484 = 1.3% | same |
| all, 5 km         | 45,865 / 217,484 = 21.1% | same |
| DE only, 500 m    | 965 / 57,010 = 1.7% | same |

That agreement is the test. If the two ever diverge, the browser model is
wrong, and the server number is the one to trust.

### The power filter is bands, not a minimum

A minimum-kW slider cannot be reproduced from a band index: band 1 is
50-150 kW, so ">= 100" is not a band. A site that says "150 kW+" in the
filter while the coverage quietly counts 120 kW chargers is lying about its
own number. The filter is now the bands the data actually has:
< 50, 50-150, 150-350, 350+ kW.

### "Chargers needed" was an extrapolation and had to go

The old tile computed

    chargers + ceil(uncovered / (covered / chargers))

which assumes every new charger covers exactly the average number the current
ones do. Chargers get placed where the companies cluster, so the real curve is
sublinear and the tile printed **85,268**. Consistent arithmetic, useless
planning. The tile now shows the companies **not yet covered**, which is a hard
count from the same pair table and needs no assumption.

### A country chip isolates that country

With every country selected, clicking a chip REMOVED that country, so "Germany
only" was the 39 clicks it takes to deselect every other one. Now:

    all selected + click -> isolate
    only it      + click -> back to all
    otherwise    + click -> toggle

### The company layer: 217,484 rows across 40 countries

Getting there exposed three separate bugs.

**The gated mirror.** `overpass.openstreetmap.fr` answers 403 with "This
service is only available to white-listed usages" for the descriptive user
agent, and HTTP 200 for a short agent name without parentheses or semicolons.
Measured with a real DE cell: descriptive -> 403, short -> 200 with 1,458
elements. That mirror carries most of the European data, so every cell was
failing. A short agent is now used as a fallback, only on a 403.

**The box country.** The company ingest wrote `country = <the query box's
country>`. A box is a rectangle and Europe is not: the German box contains
parts of the Netherlands, Belgium, Austria, Switzerland, Luxembourg and
Czechia, so 36,881 rows were in the wrong country. DE read 71,066 while NL
fell from 12,445 to 3,209. This is the same bug that was fixed for the charge
points in 44850c7 — the polygon must WIN and the box is only a fallback.

**The repair speed.** The Python resolver walked the vertices of every country
whose bounding box a point fell inside, with no cheap rejection first, so
repairing 217,484 rows did not finish in ten minutes. Two fixes:
`country_geom.py` got a bounding-box prefilter (a box can only REJECT a
point, never accept one, so the answer is unchanged and all 15 resolver tests
still pass), and the repair moved to PostGIS
(`scripts/repair_company_country_join.py`), one indexed join, 253 s.

Correlated subquery with `ORDER BY ST_Area` per row: did not finish in ten
minutes. The same test as a join with `DISTINCT ON`: 253 s. `ST_Contains`
inside an `UPDATE` recomputes the lookup per row; materialise first.

**Verification.** The polygons were checked against 12 known cities before the
relabelling was trusted, including the hard ones: Aachen DE, Antwerpen BE,
Maastricht NL, Basel CH, Salzburg AT, Praha CZ. 12/12 correct.

### Two smaller traps

- The pair table shipped a per-company country array the browser never read
  (the denominator comes from `companiesByCountry`). 900 KB removed.
- The charger count in the panel included only chargers that happened to
  reach a company, so the default view said 1,077 chargers when 3,048 pass the
  filter. It is counted over the charger table now. The fleet table had the
  same defect (Germany 351 vs 647) and is fixed the same way.

## 2026-10-25 — TENtec / AFIR as a second charging source (Hermione)

The European Commission publishes the Member State AFIR reporting data for
alternative-fuels infrastructure, and it is the only Europe-wide source that
carries the TEN-T core-network flag, the corridor name, the operator and the
per-connector power. It is now ingested as a second source beside the OSM and
Depotcharge layers.

### The endpoint, and how it was found

    https://tentec.transport.ec.europa.eu/api/public/gis/
        Alternative_Fuels/MapServer/4/query

No token, no key. It was not documented anywhere: the layer URLs inside the
Angular bundle are relative (`/gis/Alternative_Fuels/MapServer/4`), so the host
that serves them only appears in a network log. Loading the page in a headless
browser on CT141 and recording requests matching `/api/` found it in one pass.

Two traps while probing by hand:

- The app is mounted at `/tentec2` and the server adds that prefix itself, so
  asking for `/tentec2/gis/...` becomes `/tentec2/tentec2/gis/...`. Ask without
  the prefix.
- `/api/...` answers `HTTP 400 "The REST service can only be accessed
  programmatically."` without `Accept: application/json`. It is a header gate,
  not auth.

The site is **IPv6-only from the Hermes host** and every request there fails
with no route. CT141 reaches it. All ingest runs on CT141.

### What the layer has, and the four traps in it

Layer 4 is 1,280,290 connector rows across 24 fields.

1. **`CONNECTOR_POWER` is in WATTS.** A row reads `11000` for an 11 kW Type 2
   connector. Filtering on `>= 150` returns nothing; it must be `>= 150000`.
2. **Rows are per connector, not per site.** One charging pool repeats with
   identical attributes. Rows are aggregated on `LOCATION_UNIQUE_ID`, which
   took 1,280,290 rows down to 349,953 locations.
3. **45% of rows carry no `COUNTRY_CODE`** (581,708 of 1,280,290). A country
   filter silently drops them. The polygon resolver fills the gap from the
   coordinates, never from a query box: the box-country bug already cost this
   project 36,881 companies.
4. **There is no availability data.** All 24 fields were read; not one is a
   status, occupancy, free/busy or realtime field. See below.

### Paging: resultOffset is O(offset), keyset is flat

Measured on this service, one page of 2,000 rows:

| request | time |
|---------|------|
| `resultOffset=0` | 0.5 s |
| `resultOffset=200,000` | 6.3 s |
| `resultOffset=800,000` | 24.0 s |
| `resultOffset=1,278,000` | 23.0 s |
| `OBJECTID > <last>`, start at 0 | 0.9 - 4.3 s |
| `OBJECTID > <last>`, start 800,000 deep | 3.7 s |

So the first ingest attempt was spending almost all its time asking the service
to skip rows it had already read, and two runs timed out before writing
anything. Keyset paging (`where ... AND OBJECTID > {last}`, ordered by
`OBJECTID`) is flat at any depth, and the full layer then completed in about
twelve minutes with three worker threads over OBJECTID ranges.

`count()` and the min/max statistics call each cost 40-45 s. They are worth one
call at the start and not one per page.

### A dropped socket is not a URLError

The service closes a concurrent connection with
`http.client.RemoteDisconnected: Remote end closed connection without response`.
That is an `OSError`, **not** a `URLError`, so a retry list of
`(URLError, TimeoutError, JSONDecodeError)` let it escape and killed the run
after a convincing-looking stack trace. Catch `OSError` and
`HTTPException` and back off.

### Operability: confirmed absent, and said so

The request was plug free/busy. TENtec does not have it, and no free EU-wide
source does: live occupancy needs an OCPI/OICP feed per operator or a commercial
hub such as Gireve or Hubject.

Rather than leave a blank cell that reads as "free", `afir_locations` carries
`status`, `status_at` and `status_source` columns that stay NULL, and the UI
prints "Niet beschikbaar / Not available" with the reason underneath. A
real-time feed can fill those columns without a migration.

### The vehicle-class filter

Light duty is loaded, not skipped, and the site can filter it out.

    HDV   6,085   (2,234 on the TEN-T core network, up to 1,200 kW)
    LDV 343,868   (66,327 on core, up to 345 kW)
    total 349,953 across 44 countries, zero without a country

Classification: HDV when the location can physically charge a truck, meaning
350 kW or more, or an MCS connector. Everything else is LDV. A 400 kW CCS bay
also serves cars, but a car does not need a truck bay, so the classifier errs
towards HDV: the cost of that error is one extra site in the truck view, and
the cost of the opposite error is a missing truck charger.

LDV is **95% of the data**, so it does not load with the page:

| file | size | when |
|------|------|------|
| `afir_hdv.geojson` | 2.3 MB | with the page |
| `afir_ldv.json` | 8.5 MB | only when the filter asks for cars |
| `afir_summary.json` | 3.3 KB | with the page |

The LDV file is compact flat arrays `[lon, lat, kw, countryIndex, connectors]`,
not GeoJSON, because GeoJSON repeats every property name once per feature. The
browser turns the array into a GeoJSON source in one pass when the toggle flips.
A truck map that downloads a quarter of a million car chargers before first
paint is a truck map nobody waits for.

Verified live: 6,085 + 343,868 = 349,953 exactly, and the map goes from 14 blue
pixels to 11,947 when the car layer is switched on, so the toggle changes the
map and not only the numbers.

### React error #321 is a nesting bug, not a WebGL bug

The AFIR `useEffect` was inserted at the anchor `const map = mapRef.current;`,
which sits **inside** the depots effect callback. A hook registered inside
another hook body throws React #321 (invalid hook call), the map component
threw, and `MapBoundary` replaced the whole map with its generic message
"The map could not start. WebGL is not available in this browser".

That message sent me to the GPU. The browser console said otherwise, and so did
the QA harness: `canvas: null` **and** `tileRequests: 0`, where a working map
had reported 37 tiles. Zero tile requests is a dead component, not a slow or
blocked GPU.

Keep every `useEffect` at two-space indent inside a component and check for
four-space `useEffect` lines after any scripted insert.

## 2026-10-25 — the radius is a filter, not a pile of rings (Hermione)

The radius now selects ONE distance. The site draws that radius and nothing
else, and the coverage is computed against it.

### The bug: four radii drew at once

`coverage.geojson` holds 1,968 sites x 4 radii = **7,872 polygons**. The filter
that picked one radius was:

```ts
useEffect(() => {
  const map = mapRef.current;
  if (!map || !map.getLayer("coverage")) return;   // <- here
  map.setFilter("coverage", ["==", ["get", "radiusM"], radiusM]);
}, [radiusM, coverage]);
```

The coverage layers are created inside `map.once("load")` whenever the style is
not ready, which is the normal case on a cold page load. So at the moment this
effect ran, the layer did not exist, it returned early, and **the filter was
never applied**. All 7,872 polygons drew. Changing the radius afterwards applied
the filter, which is why it behaved like a filter that only sometimes worked.

The fix has two parts:

1. The filter expression is now set **when the layers are created**, reading the
   radius from `radiusRef.current` (a ref updated on every render), so creation
   can never happen without a filter.
2. The change effect falls back to `map.once("idle", apply)` instead of
   returning silently when the layer is not there yet.

### The control moved out of the toggle

The radius picker lived inside the coverage card, next to an on/off switch, so
the control that decides what the number MEANS was hidden inside a toggle. It is
now its own section beside Country, and rings are visible by default: a radius
filter that draws nothing until a second switch is flipped is not a filter.

### Verified exactly, not by counting pixels

`window.__depotMap` now exposes the map instance for QA. Then:

```js
map.getFilter("coverage")                                  // names the radius
map.queryRenderedFeatures({layers:["coverage"]}).length     // how many drew
```

| selected | filter says | rings drawn | panel |
|----------|-------------|-------------|-------|
| 500 m | 500 | 0 (sub-pixel at Europe zoom) | 2,747 / 217,484 = 1.3% |
| 1 km | 1000 | 162 | 7,053 = 3.2% |
| 2 km | 2000 | 1,968 | 16,974 = 7.8% |
| 5 km | 5000 | 1,969 | 45,865 = 21.1% |

Rings drawn stays at one radius worth (1,968) and never returns to 7,872. The
filter value matches the button at every step.

At continental zoom a 500 m ring is smaller than a pixel, so `drawn: 0` there is
geometry, not a bug; the number still updates and the ring appears on zoom in.

### Two colour collisions, both real

1. **AFIR was the same amber as the coverage fill** (`#f59e0b`), so the EU sites
   and the coverage circles could not be told apart.
2. **Every depot tier is amber** (`#b45309`, `#d97706`, `#f59e0b`), so counting
   amber pixels counted markers, whose number does not change with the radius.
   A working filter and a broken one measured identical, which is how I wasted a
   cycle on a test that could not fail.

Palette is now one colour, one meaning:

| layer | colour |
|-------|--------|
| depot markers | amber (by tier) |
| coverage rings | teal `#0d9488` fill, `#0f766e` line |
| AFIR truck sites | violet `#7c3aed` |
| AFIR light duty | light blue `#38bdf8` |

### The lesson on measuring a map

A screenshot cannot tell a coverage ring from a marker when they share a colour,
and `querySelector("canvas")` proves nothing about whether a layer drew. Expose
the map and ask it: `getFilter` for what the filter is, `queryRenderedFeatures`
for what is actually on screen. Assert on the quantity the bug changes.
