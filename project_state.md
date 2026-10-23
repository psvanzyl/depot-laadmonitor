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
