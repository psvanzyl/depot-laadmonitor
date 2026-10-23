# eTrucker as a data source

Status: LIVE (snapshot ingest wired into the daily cycle)
Date: 2026-10-23

## What this source is

eTrucker publishes charging locations for heavy vehicles across Europe. The phone
app is Flutter, has no hardcoded API host, and gets its backend details at runtime
through Firebase Remote Config. Static analysis of the APK therefore found no URL
to call, and the data could not be reached from the Hermes host or with an
unauthenticated request.

The data is reachable a different way. The app ships its Firebase **client** key
(public by design; every install carries it) for project `etrucker-prod`. The app
signs in **anonymously** at first launch and then reads the Firestore collection
`location`. An anonymous session created with that same client key reads the same
collection. No account, password or token of a person is involved, and the access
is read-only.

    project:     etrucker-prod
    collection:  location        (readable by an anonymous Firebase user)
    also open:   poi             (not used: no address or coordinates)
    locked:      listCollectionIds, storage listing  -> 403

## Why it is worth having

Each document carries a structured `heavy_vehicles` block:

    heavy_vehicles_dedicated            a bay dedicated to trucks
    heavy_vehicles_drive_through        can drive through, no reversing
    heavy_vehicles_rigid_vehicles       rigid truck fits
    heavy_vehicles_trailer_accessibility  trailer access
    heavy_vehicles_dangerous_goods      ADR allowed
    heavy_vehicles_overnight_parking    overnight parking allowed
    heavy_vehicles_restrictions         N1 / N2 / N3 codes

That is the operator-side statement that a truck fits — the same class of evidence
as Depotcharge's `truckCompatibility`, and far stronger than a name or a power
guess. Coordinates are exact, and connectors carry `max_power` in watts, plus
voltage and amperage.

## How a refresh works

1. The client key lives at `/root/.etrucker/firebase_key` (mode 0600) on CT141,
   never in the repo and never in chat.
2. Run `scripts/etrucker_snapshot.py` from the project root. It signs in
   anonymously, pages through `location` per country, applies the truck gate and
   the coordinate check, and writes only the reduced records to
   `data/etrucker/locations.json`.
3. The daily cycle reads that snapshot only. An expired key, a changed security
   rule or a rate limit cannot break the scheduled run: a missing snapshot marks
   the source `skipped`.

Country list is set in the script (`DEFAULT_COUNTRIES`), currently
NLD,DEU,BEL,AUT,CHE,ESP,ITA,LUX,LIE,FRA,DNK.

## Gate

A document becomes a signal only when at least one of these is true:

- any `heavy_vehicles_*` boolean flag is true
- a restriction code is N2 or N3
- an MCS connector is present

Otherwise it is dropped, even if it is a 400 kW public hub. This is the same
"explicit truck evidence" rule the rest of the map uses.

## Files

    scripts/etrucker_snapshot.py        capture + reduce (network, run by hand)
    src/depotmon/ingest/etrucker.py     snapshot ingest (never touches network)
    tests/test_etrucker.py              8 tests on the gate and signal shape
    data/etrucker/locations.json        snapshot (gitignored)

## Known limits

- The truck flags are eTrucker's own aggregation of CPO data. A missing flag does
  not prove a site is unsuitable; it only means nobody declared it.
- `access` is taken from the document `type` field and defaults to `public`;
  a private depot wrongly marked public would inflate the public count.
- One Firestore document per site, but a site can hold hundreds of EVSEs
  (max seen: 591 in one Dutch location), so `n_points` is a connector count,
  not a bay count.
