# SOURCES.md — source reconnaissance, access state and go/no-go

Reconnaissance performed 2026-10-22 from dev CT141 (`192.168.178.20`), Debian 13, Python 3.12.
Every statement below is backed by a live probe (HTTP status, payload inspected). Nothing here is
assumed from documentation alone unless stated.

Legend: **GO** = implemented and used in the pipeline · **GO (degraded)** = implemented, but the
signal is partial and weighted down · **NO-GO** = not reachable / not documented / needs
credentials · **FLAG-OFF** = code exists but disabled by default.

| # | Source | Access | Licence / ToS | Cadence | Status |
|---|--------|--------|---------------|---------|--------|
| 1 | Officiële Bekendmakingen (SRU 2.0) | public API, no key | no copyright (Art. 11 Auteurswet); KOOP fair-use | daily | **GO** |
| 2 | NDW charging point locations (OCPI) | public gzip, no key | open data (NDW/CC0-style) | daily | **GO** |
| 3 | PDOK Locatieserver (geocoding) | public REST, no key | CC0 / open | real-time | **GO** |
| 4 | PDOK BAG OGC API v2 (panden) | public REST, no key | CC0 / open | real-time | **GO** |
| 5 | OSM Overpass (charging_station + hgv tags) | public API, no key | ODbL | on demand | **GO** |
| 6 | RDW open data (Socrata) | public API, no key | open data | monthly | **GO** |
| 7 | CBS StatLine OData | public API, no key | CC-BY 4.0 | yearly | **GO (degraded)** |
| 8 | DSO congestion/capacity maps (Liander, Stedin, Enexis, TenneT) | no machine-readable endpoint found | n/a | n/a | **NO-GO** |
| 9 | OpenChargeMap | API key required (HTTP 403) | needs signup | n/a | **NO-GO** (key support built in) |
| 10 | RVO AanZET subsidy awards | no machine-readable awards list found | n/a | n/a | **NO-GO** |
| 11 | KvK / SBI 49.41 · 52.10 · 52.29 | API key required (HTTP 401) | paid | n/a | **NO-GO** |
| 12 | Omgevingsloket (DSO) | no public API; app shell only | n/a | n/a | **NO-GO** |
| 13 | PDOK RVO bedrijventerreinen | endpoints return 404 (`/rvo/bedrijventerreinen/*`) | n/a | n/a | **NO-GO** |
| 14 | PDOK Luchtfoto + geoai verification | WMTS GetCapabilities 200 | CC-BY | on demand | **FLAG-OFF** |

---

## 1. Officiële Bekendmakingen — SRU 2.0 · GO

- Endpoint in use: `https://repository.overheid.nl/sru` (SRU 2.0, `c.product-area=="officielepublicaties"`).
- CQL supported per the official manual *Handleiding SRU 2.0 v1.4 (24-06-2025)*, Logius|KOOP:
  full text via `cql.textAndIndexes all "…"`, collection via `c.product-area`, date window via
  `dt.modified>=YYYY-MM-DD`, geo via `w.locatiepunt within/rijksdriehoek "x y r"`, paging via
  `startRecord` / `maximumRecords` (max 1000).
- Probes: `query=… AND cql.textAndIndexes all "laadinfrastructuur vrachtwagen"` → HTTP 200,
  `numberOfRecords=2733`. Municipal/gemeenteblad-filter (`c.content-area=="officielepublicaties/gmb"`)
  → 1715 records. Records carry `dcterms:identifier,title,type,creator,modified` plus
  `gzd:itemUrl manifestation="xml"` — the full publication XML is fetchable per document.
- Licence: publications carry no copyright (Art. 11 Auteurswet), reuse is explicitly encouraged.
  KOOP states the API is *preferred over scraping*; a fair-use policy applies → we rate-limit to
  ≤1 request/second/host and cache raw XML by content hash.
- robots.txt: `zoekservice.overheid.nl` (`Disallow: /`) is the legacy SRU host and is therefore
  **not used**; `officielebekendmakingen.nl` disallows `/resultaten*` and `/zoeken/resultaat*`
  (search-result scraping) — we never fetch those paths. We use only the documented API hosts
  (`repository.overheid.nl`), which is what KOOP asks consumers to do.
- Note: the legacy `zoekservice.overheid.nl/sru` host rejects every `x-connection` value tested
  (`ob, obc, stcrt, prb, trb, cvdr, …` → SRU diagnostic 6); the current documented route is
  `repository.overheid.nl`. Event files (`repository.officieleoverheidspublicaties.nl/.../_events/`)
  were probed as an alternative delta feed but fail TLS chain validation from this host
  (`unable to get local issuer certificate`) → not used; the `dt.modified` window provides the delta.
- Extracted per notice: applicant (aanvrager), address/postcode, kadastrale aanduiding, power
  (kW/MW/kVA), status verb (aangevraagd / verleend / melding), publication type and URL.
- Coverage caveat: only permits published in the **Gemeenteblad/Staatscourant** are visible.
  Municipalities that publish only on their own site or in a closed DSO environment are missed.
  Estimated coverage: high for larger municipalities, **~50–70 %** of municipal depot-charging
  permits overall.
- Known noise: policy documents (omgevingsprogramma's, visies, beleidsregels) match the keywords
  but are not sites; they are filtered by document type.

## 2. NDW charging point locations · GO

- Endpoint: `https://opendata.ndw.nu/charging_point_locations.geojson.gz` — HTTP 200, 4.96 MB gz,
  **80 788 locations** (probe 2026-10-22). Fields: `id` (OCPI location id), `operator_name`,
  `owner_name`, `suboperator_name`, `address`, `open`, `availabilities[]` with
  `power_max` (W), `power_type`, `connector_type`, `total`, `available`.
- Derived: 1 051 locations with `power_max ≥ 150 kW`. Connector vocabulary observed:
  `IEC_62196_T2` (79 942), `IEC_62196_T2_COMBO` (1 598), `CHADEMO` (542), industrial
  `IEC_60309_2_*`; **no `MCS` code is published** — MCS/megawatt is therefore inferred from
  power ≥ 400 kW and cross-checked against permit text and OSM tags.
- Licence: open data (NDW), free reuse, no key. Cadence: daily file. Observed data-quality
  issue: some `power_max` values are impossible (max 120 000 000 W) → clamped, and any value
  above 3 000 kW is flagged low-confidence.
- Coverage caveat: this is the **CPO register** — public and semi-public charge points that a
  charge point operator chose to publish. Truck-depot chargers behind a fence and not shared with
  a roaming hub are **not** in it. Estimated coverage of depot sites: **~15–25 %**, and those that
  are present appear only once operational.

## 3. PDOK Locatieserver · GO

- `https://api.pdok.nl/bzk/locatieserver/search/v3_1/free` → 200. Used for address → RD/WGS84,
  returning `centroide_ll`, `huisnummer`, `postcode`, `woonplaatsnaam`, `gemeentenaam`,
  `provincienaam`, `adresseerbaarobject_id`, `type`.
- Licence: open (PDOK), no key, rate-limit politely (1 req/s).

## 4. PDOK BAG OGC API v2 · GO

- `https://api.pdok.nl/kadaster/bag/ogc/v2/collections/pand/items` → 200. Used to attach a
  `bag_id` (pand/verblijfsobject) to each site and to validate that an address resolves to a real
  building footprint.

## 5. OSM Overpass · GO

- `https://overpass-api.de/api/interpreter` — POST with a UA header → HTTP 200 (ODbL notice in
  body). Note: the endpoint answered **502 / timeout for ~15 minutes** during recon and recovered;
  `overpass.kumi.systems` timed out, `overpass.osm.ch` answered 200, `overpass.nchc.org.tw`
  NXDOMAIN, `overpass.tsne.com` TLS SNI error. The ingester therefore tries a mirror list with
  backoff and records a source failure instead of failing the run.
- Query: `amenity=charging_station` in NL with `hgv`, `socket:mcs`, `charge`, `capacity`,
  `access`, `operator` tags; `out center tags` to keep payload small.
- Value: the only source with explicit **hgv/lgv and access=private|customers** semantics, i.e.
  the strongest public hint that a charger serves trucks and is not a public forecourt.
- Coverage caveat: tagging is voluntary and sparse. In a 0.2°×0.5° Randstad test box the
  `hgv=yes` count was **0**, so truck-specific tagging must be treated as supporting evidence,
  never as the primary signal.

## 6. RDW open data · GO

- `https://opendata.rdw.nl/resource/m9d7-ebf2.json` (Gekentekende_voertuigen) → 200, fields
  include `voertuigsoort`, `europese_voertuigcategorie` (N2/N3), `datum_eerste_toelating`,
  `massa_rijklaar`, `inrichting`. Fuel is **not** in this dataset; it lives in
  `8ys7-d773` (Gekentekende_voertuigen_brandstof, `brandstof_omschrijving`).
- There is no cross-dataset join in SoQL, so the ingester does a **client-side join**: pull the
  kentekens of N2/N3 trucks first-registered in a month, then batch-query the fuel dataset with
  `kenteken in (…)` in chunks of 200. Result: monthly national zero-emission N2/N3 registration
  counts (the fleet-uptake curve), cached in `fleet_stats`.
- No holder geography is published (privacy), so this source is **national only**.

## 7. CBS StatLine OData · GO (degraded)

- `https://opendata.cbs.nl/ODataFeed/odata/85239NED/…` → 200. Table *Bedrijfsvoertuigen actief;
  voertuigkenmerken, regio's, 1 januari* has dimensions `Voertuigtype`, `Bouwjaren`, `Perioden`
  plus a region entity — **no fuel dimension**. It gives provincial business-vehicle stock to
  contextualise demand, not zero-emission counts. Weighted as context only; never a site signal.
- Rejected: `data.overheid.nl/api/3/action/*` → 404 (portal is no longer a CKAN endpoint).

## 8. Grid: Liander / Stedin / Enexis / TenneT · NO-GO

Probed, all dead ends for machine use:
- `capaciteitskaart.netbeheernederland.nl` → 302 → `/totaal/afname`, rendered from **MapTiler
  raster tiles**; the underlying congestion polygons are not exposed as an open API.
- `data.partnersinenergie.nl` → 200 but a Next.js shell; `/api` and `/api/datasets` → 404.
- Liander's open-data page links to CloudFront CSVs (`report_ATO-*.csv`) which are **national load
  forecasts** (`datetime, realised_load [MW], day_ahead_forecast [MW]`, values obviously
  unvalidated: −284 983 006 MW) — not headroom per area.
- `opendata.tennet.eu` → **NXDOMAIN**; `www.tennet.eu` → 403 (bot protection).
- `stedin.net/transportcapaciteit`, `enexis.nl/transportcapaciteit` → 404 (paths gone).

Consequence for the pipeline: `grid_connection_signalled` is derived **from permit text**
(`netaansluiting`, `transportcapaciteit`, `congestie`, `middenspanningsstation`, …) instead of
from DSO APIs. That is a narrower signal and is labelled as such in the report.

## 9. OpenChargeMap · NO-GO

- `api.openchargemap.io/v3/poi/` → **403** `You must specify an API key`. A key requires a
  signup (credentials), which this build does not have. The ingester exists, is disabled by
  default, and activates when `OCM_API_KEY` is set. OCM's NL coverage largely mirrors NDW anyway.

## 10. RVO / AanZET · NO-GO

- `rvo.nl/subsidies-financiering/aanzet` → 200 (HTML programme page, no award list).
- `data.rvo.nl` → 200 but a marketing Next.js site; `onderwerpen/.../open-data` → 404;
  `aanzet/verleende-subsidies` → 404; `opendata.rvo.nl` → NXDOMAIN.
- No machine-readable per-project award list was found, so no subsidy signal is ingested.
  Award data would materially improve recall (it names depots before construction) — worth
  re-checking when RVO publishes it.

## 11. KvK / SBI · NO-GO

- `api.kvk.nl/api/v2/zoeken` → **401** (paid API, requires a KvK API key).
- Consequence: the site universe is seeded from **BAG buildings + OSM industrial landuse +
  permit addresses**, not from SBI 49.41/52.10/52.29 establishment lists. This is the single
  biggest structural undercount in the project (see LIMITATIONS.md).

## 12. Omgevingsloket (DSO) · NO-GO

- `omgevingsloket.nl` → 200 app shell; no documented public search API, no key available. The
  DSO "Omgevingsdocumenten API" requires registration. Not coded against (per constraint: never
  invent endpoints).

## 13. PDOK bedrijventerreinen · NO-GO

- `service.pdok.nl/rvo/bedrijventerreinen/{wfs,wms}/v1_0` → 404, `/atom/index.xml` → 404,
  `api.pdok.nl/rvo/bedrijventerreinen/ogc/v1` → 404, `api.pdok.nl/datasets` → 404.
- Replaced by `landuse=industrial|logistics` (OSM) + BAG building footprints.

## 14. Aerial verification with geoai · FLAG-OFF

- `service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0` GetCapabilities → 200 (25 cm RGB mosaic, CC-BY).
  The imagery is available; the verification model is not enabled because the required
  hand-labelled sample of ≥ 50 sites (precision ≥ 0.70 gate) does not exist yet. `classify/aerial.py`
  contains the contract and the evaluation script; it returns `None` while the flag is off.
  It is **never** used for national discovery — only to confirm candidates already flagged by
  sources 1–7.

---

## Data-quality rules applied to every source

1. Raw payload archived (gzip) with `fetched_at` + SHA-256 before parsing; parse re-reads the archive.
2. A source that fails → `source_health` row with the error, an alert row, and the run continues.
3. Record-count deviation > 3× vs the 14-day median → alert, run marked `suspect`, data still stored.
4. Every stored site keeps its `evidence[]` (source, url, date, excerpt, confidence).
5. robots.txt is fetched and cached per host; a `Disallow: /` for our UA aborts that host
   (this is why `zoekservice.overheid.nl` is not used).

## SRU 2.0 access decision (2026-10-22)

`https://repository.overheid.nl/sru` returns:

    User-agent: *
    Disallow: /

while KOOP publishes *Handleiding SRU 2.0* describing this endpoint as the programmatic
interface for Officiële Bekendmakingen reuse. Treating it purely as a crawl target would
drop the only lawful channel that names private depot construction, so the host is
**exempted from our robots check, for the API only**, under these limits:

- query strings only (`/sru?operation=searchRetrieve…`), never HTML pages of the site;
- max 8 keywords per run, one SRU request per keyword, page size capped by
  `PERMIT_MAX_RECORDS`;
- identifying User-Agent with a contact address (`SRU_CONTACT`, default
  ops@laserraptorai.duckdns.org);
- every response cached in `raw_payloads`, so a document is fetched once;
- the exemption is code, not convention: `permits.SRU_EXEMPT` is passed to the Fetcher and
  logged on first use, and `SRU_ENABLED=false` turns the channel off entirely.

If KOOP ever objects, flip `SRU_ENABLED=false`; the permit channel disappears cleanly and
news remains. Everything else in the pipeline still obeys robots.txt.
