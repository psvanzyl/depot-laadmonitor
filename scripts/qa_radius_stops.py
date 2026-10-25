#!/usr/bin/env python3
"""Walk the radius slider through all five stops and check what actually draws.

Answers two questions per stop:
  1. Does the coverage panel agree with the database? The server-side counts for
     "every charger, every source, no attribute filter" are hardcoded below;
     they came from the same SQL the exporter runs.
  2. Does the map draw exactly the selected radius, and nothing else?

The second one needs a real WebGL context. The Hermes browser backend runs with
WebGL disabled and reports MapLibre's boundary as "WebGL is not available", so
this runs on CT141 with Xvfb and SwiftShader over CDP.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

URL = "https://depot.laserraptorai.duckdns.org/?lang=nl"
PORT = 9224

# SELECT count(DISTINCT company_id) FROM coverage_chargers p JOIN companies c
#   ON ST_DWithin(p.geog, c.geom::geography, r)   -- for r in this list
EXPECTED = {1000: 17013, 2500: 48201, 5000: 83051, 10000: 127069, 20000: 171099}
DENOMINATOR = 230904

CHROME = [
    "chromium", "--no-sandbox", "--disable-dev-shm-usage", "--disable-quic",
    "--ignore-certificate-errors", "--use-gl=angle", "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader", f"--remote-debugging-port={PORT}",
    "--remote-allow-origins=*", "--remote-debugging-address=127.0.0.1",
    "--user-data-dir=/tmp/qa-stops", "--no-first-run", "--hide-scrollbars",
]

READ = r"""(() => {
  const panel=[...document.querySelectorAll('aside section')]
    .find(x=>/DEKKING/i.test(x.textContent||''));
  const dd=panel? [...panel.querySelectorAll('dd')].map(d=>d.textContent.trim()):null;
  const m=window.__depotMap;
  const out={dd};
  if(m){
    try { out.rendered = m.queryRenderedFeatures({layers:['coverage']}).length; }
    catch(e){ out.rendered='err'; }
    try {
      const src=m.getSource('coverage');
      out.sourceFeatures = src && src._data ? src._data.features.length : null;
    } catch(e){ out.sourceFeatures='err'; }
    out.radiiDrawn = (() => {
      try {
        const f=m.queryRenderedFeatures({layers:['coverage']});
        return [...new Set(f.map(x=>x.properties.radiusM))];
      } catch(e){ return 'err'; }
    })();
  }
  return JSON.stringify(out);
})()"""

SET = """(() => {
  const r=document.querySelector('aside input[type=range]');
  const setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
  setter.call(r,'%d');
  r.dispatchEvent(new Event('input',{bubbles:true}));
  return r.value;
})()"""


def http(path: str, method: str = "GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def wait_cdp(timeout: float = 40) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            http("/json/version")
            return True
        except Exception:
            time.sleep(1)
    return False


def main() -> int:
    xvfb = subprocess.Popen(["Xvfb", ":98", "-screen", "0", "1920x1200x24"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    chrome = subprocess.Popen(CHROME + ["about:blank"],
                              env={"DISPLAY": ":98", "PATH": "/usr/bin:/bin"},
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_cdp():
            print("FAIL chromium")
            return 1
        import websocket

        tab = http("/json/new?about:blank", method="PUT")
        conn = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=90)
        seq = [0]

        def send(method, params=None):
            seq[0] += 1
            mid = seq[0]
            conn.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(conn.recv())
                if msg.get("id") == mid:
                    return msg.get("result", {})

        def ev(expr):
            r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            return r.get("result", {}).get("value")

        send("Page.enable")
        send("Runtime.enable")
        send("Emulation.setDeviceMetricsOverride",
             {"width": 1600, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
        send("Page.bringToFront")
        send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        send("Page.navigate", {"url": URL})
        time.sleep(18)

        canvas = ev("!!document.querySelector('.maplibregl-canvas')")
        print(f"map canvas: {canvas}")
        if not canvas:
            print("map text:", ev("(document.querySelector('main')||{}).innerText"))
            return 1

        bad = 0
        for i, (radius, want) in enumerate(EXPECTED.items()):
            ev(SET % i)
            time.sleep(7)
            raw = ev(READ)
            d = json.loads(raw)
            shown = (d.get("dd") or [""])[0]
            shown_n = int(shown.replace(".", "").replace(",", "")) if shown else -1
            ok = shown_n == want
            bad += 0 if ok else 1
            pct = round(100 * want / DENOMINATOR, 1)
            print(f"{'OK ' if ok else 'BAD'} {radius:>6} m  panel={shown:<9} "
                  f"expected={want:<7} ({pct}%)  rings={d.get('rendered')} "
                  f"radii={d.get('radiiDrawn')} srcFeats={d.get('sourceFeatures')}")

        print("RESULT:", "all stops correct" if bad == 0 else f"{bad} stop(s) wrong")
        return 0 if bad == 0 else 1
    finally:
        chrome.terminate()
        xvfb.terminate()


if __name__ == "__main__":
    sys.exit(main())
