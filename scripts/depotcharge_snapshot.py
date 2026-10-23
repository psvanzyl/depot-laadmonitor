#!/usr/bin/env python3
"""Refresh the Depotcharge snapshot that the depotcharge ingest reads.

Depotcharge has no public API and the fleet map sits behind a login, so this is
NOT part of the scheduled cycle. Run it by hand when the snapshot goes stale:

  1. Start Chromium with the remote debugging port and log in to
     https://hub.depotcharge.com/auth once (the session is kept in the profile).
  2. ./scripts/run.sh scripts/depotcharge_snapshot.py
  3. Review the numbers it prints, then let the normal cycle run.

It writes:
    data/depotcharge/locations.json     map-locations, all pages
    data/depotcharge/chargepoints.json  per-location chargepoints (NL only)

Politeness: 1 request/second for chargepoints, and it stops immediately on
401/403/429 rather than retrying. Nothing here runs on a timer.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import urllib.request

import websockets

CDP = os.environ.get("CDP_URL", "http://127.0.0.1:9222")
ORG = os.environ.get("DEPOTCHARGE_ORG", "4a918bfe-c0a2-40f7-9b43-a29e280e1d2e")
FLEET = os.environ.get("DEPOTCHARGE_FLEET", "52c2bebe-2419-4954-a079-8a625844abea")
OUT = pathlib.Path(os.environ.get("DEPOTCHARGE_DIR", "data/depotcharge"))
BASE = "https://api.depotcharge.com/api/bff/app/v1"
NL = {"Nederland", "Niederlande", "Netherlands", "NL"}
DELAY = 1.0


async def main() -> None:
    with urllib.request.urlopen(f"{CDP}/json/list", timeout=8) as f:
        pages = [t for t in json.load(f) if t.get("type") == "page"]
    ws = await websockets.connect(pages[0]["webSocketDebuggerUrl"], max_size=2 ** 28)

    n = {"i": 0}
    pend: dict[int, asyncio.Future] = {}

    async def reader():
        while True:
            try:
                m = json.loads(await ws.recv())
            except Exception:
                return
            if "id" in m and m["id"] in pend:
                pend.pop(m["id"]).set_result(m)

    async def call(method, params=None, tmo=120):
        n["i"] += 1
        fut = asyncio.get_event_loop().create_future()
        pend[n["i"]] = fut
        await ws.send(json.dumps({"id": n["i"], "method": method, "params": params or {}}))
        return (await asyncio.wait_for(fut, tmo)).get("result", {})

    task = asyncio.create_task(reader())
    await call("Runtime.enable")

    async def get(path: str):
        expr = """(async () => {
          const tok = localStorage.getItem('authToken');
          if (!tok) return JSON.stringify({status: 0, body: 'no authToken — log in first'});
          const r = await fetch(%s, {headers: {Authorization: 'Bearer ' + tok, accept: 'application/json'}});
          return JSON.stringify({status: r.status, body: await r.text()});
        })()""" % json.dumps(BASE + path)
        res = await call("Runtime.evaluate",
                         {"expression": expr, "awaitPromise": True, "returnByValue": True})
        raw = res.get("result", {}).get("value")
        if not raw:
            raise RuntimeError("evaluate returned nothing — is the browser logged in?")
        return json.loads(raw)

    # 1. locations, paged
    rows, page = [], 0
    while True:
        d = await get(f"/organizations/{ORG}/fleets/{FLEET}/map-locations"
                      f"?page={page}&size=200&sort=id%2Casc")
        if d["status"] != 200:
            raise RuntimeError(f"locations HTTP {d['status']}: {d['body'][:200]}")
        payload = json.loads(d["body"])
        content = payload.get("content") or []
        rows.extend(content)
        total = payload.get("totalElements") or 0
        print(f"  locations page {page}: +{len(content)} ({len(rows)}/{total})")
        if not content or len(rows) >= total:
            break
        page += 1

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "locations.json").write_text(json.dumps(rows))
    print(f"wrote {len(rows)} locations -> {OUT/'locations.json'}")

    # 2. chargepoints, NL only, throttled
    nl = [r for r in rows
          if (r["location"].get("address") or {}).get("country") in NL
          and (r.get("connectorsCount") or 0) > 0]
    print(f"fetching chargepoints for {len(nl)} NL locations at {DELAY}s intervals")
    cps: dict[str, list] = {}
    for i, r in enumerate(nl, 1):
        lid = r["location"]["id"]
        d = await get(f"/organizations/{ORG}/fleets/{FLEET}/map-locations/{lid}/chargepoints")
        if d["status"] == 200:
            cps[lid] = json.loads(d["body"])
        elif d["status"] in (401, 403, 429):
            print(f"STOP at {i}: HTTP {d['status']} — keeping {len(cps)}")
            break
        if i % 20 == 0:
            print(f"  {i}/{len(nl)}")
        await asyncio.sleep(DELAY)

    (OUT / "chargepoints.json").write_text(json.dumps(cps))
    print(f"wrote {len(cps)} chargepoint sets -> {OUT/'chargepoints.json'}")
    task.cancel()
    await ws.close()


asyncio.run(main())
