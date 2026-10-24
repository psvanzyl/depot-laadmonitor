#!/usr/bin/env python3
"""QA the DepotChargeDex map with a real WebGL browser, on CT141.

The Hermes browser backend runs with WebGL disabled, so MapLibre shows its
error boundary and the map cannot be photographed there. CT141 has Chromium
and Xvfb, so the map is rendered there over CDP and screenshotted.

Usage:
    scripts/qa_map.py [--url URL] [--out DIR]

Produces:
    DIR/desktop.png   1600x1000
    DIR/mobile.png    390x844
    DIR/report.json   the measurements behind the screenshots
"""
from __future__ import annotations

import base64
import json
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

URL = "https://depot.laserraptorai.duckdns.org/"
OUT = pathlib.Path("/root/projects/depot-laadmonitor/scratch/qa")
PORT = 9223

CHROME_FLAGS = [
    "chromium",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-quic",
    "--ignore-certificate-errors",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    f"--remote-debugging-port={PORT}",
    # Chrome rejects a websocket whose Origin header it does not expect.
    "--remote-allow-origins=*",
    "--remote-debugging-address=127.0.0.1",
    "--user-data-dir=/tmp/qa-chrome",
    "--no-first-run",
    "--hide-scrollbars",
]


def cdp(path: str, method: str = "GET", body: dict | None = None, timeout: float = 30.0):
    """Call the CDP HTTP endpoint."""
    url = f"http://127.0.0.1:{PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def wait_for_cdp(timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            cdp("/json/version", timeout=5)
            return True
        except Exception:
            time.sleep(1)
    return False


def main() -> int:
    global URL, OUT
    args = sys.argv[1:]
    if "--url" in args:
        URL = args[args.index("--url") + 1]
    if "--out" in args:
        OUT = pathlib.Path(args[args.index("--out") + 1])
    OUT.mkdir(parents=True, exist_ok=True)

    # Start Xvfb + Chromium.
    xvfb = subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1920x1200x24"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    chrome = subprocess.Popen(CHROME_FLAGS + ["about:blank"],
                              env={"DISPLAY": ":99", "PATH": "/usr/bin:/bin"},
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_for_cdp():
            print("FAIL: chromium did not come up")
            return 1

        report = {}
        for name, w, h, mobile in (("desktop", 1600, 1000, False),
                                    ("mobile", 390, 844, True)):
            tab = cdp("/json/new?about:blank", method="PUT")
            ws = tab["webSocketDebuggerUrl"]
            # Drive the tab over a raw websocket: the HTTP endpoint cannot
            # send Page.* commands.
            import websocket  # noqa: F401  (websocket-client)

            conn = websocket.create_connection(ws, timeout=60)
            seq = [0]

            def send(method, params=None, wait=True):
                seq[0] += 1
                mid = seq[0]
                conn.send(json.dumps({"id": mid, "method": method,
                                      "params": params or {}}))
                if not wait:
                    return None
                while True:
                    msg = json.loads(conn.recv())
                    if msg.get("id") == mid:
                        return msg.get("result", {})

            send("Page.enable")
            send("Runtime.enable")
            send("Emulation.setDeviceMetricsOverride",
                 {"width": w, "height": h, "deviceScaleFactor": 2 if mobile else 1,
                  "mobile": mobile})
            # A hidden tab never paints, and MapLibre only loads its style on
            # the first rendered frame. Force it visible.
            send("Page.bringToFront")
            send("Emulation.setFocusEmulationEnabled", {"enabled": True})
            send("Page.navigate", {"url": URL})
            time.sleep(9)

            def evaluate(expr):
                r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
                return r.get("result", {}).get("value")

            metrics = evaluate("""(() => {
              const main = document.querySelector('main');
              const aside = document.querySelector('aside');
              const canvas = document.querySelector('canvas');
              const cb = canvas ? canvas.getBoundingClientRect() : null;
              const tiles = performance.getEntriesByType('resource')
                             .filter(e => e.name.includes('tile.openstreetmap.org')).length;
              return JSON.stringify({
                visibility: document.visibilityState,
                title: document.title,
                mainTop: main ? Math.round(main.getBoundingClientRect().top) : null,
                mainBottom: main ? Math.round(main.getBoundingClientRect().bottom) : null,
                asideTop: aside ? Math.round(aside.getBoundingClientRect().top) : null,
                canvas: cb ? {w: Math.round(cb.width), h: Math.round(cb.height)} : null,
                tileRequests: tiles,
                overflowX: document.documentElement.scrollWidth > window.innerWidth,
                bodyScrollH: document.body.scrollHeight,
                siteCount: (document.body.innerText.match(/([\\d.]+)\\s*laadlocaties met bewijs/)||[])[1] || null,
              });
            })()""")
            report[name] = json.loads(metrics) if metrics else {"error": "no metrics"}

            shot = send("Page.captureScreenshot", {"format": "png"})
            png = base64.b64decode(shot["data"])
            (OUT / f"{name}.png").write_bytes(png)
            report[name]["screenshot"] = str(OUT / f"{name}.png")
            report[name]["screenshotBytes"] = len(png)
            conn.close()

        (OUT / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return 0
    finally:
        chrome.terminate()
        xvfb.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
