#!/usr/bin/env python3
"""Mobile check at 390x844: map first, slider reachable, nothing overflows."""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request

URL = "https://depot.laserraptorai.duckdns.org/?lang=nl"
PORT = 9225
CHROME = [
    "chromium", "--no-sandbox", "--disable-dev-shm-usage", "--disable-quic",
    "--ignore-certificate-errors", "--use-gl=angle", "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader", f"--remote-debugging-port={PORT}",
    "--remote-allow-origins=*", "--remote-debugging-address=127.0.0.1",
    "--user-data-dir=/tmp/qa-mobile", "--no-first-run", "--hide-scrollbars",
]

CHECK = r"""(() => {
  const c = document.querySelector('.maplibregl-canvas');
  const r = document.querySelector('aside input[type=range]');
  const sec = r ? r.closest('section') : null;
  const box = (e) => e ? (b => ({top:Math.round(b.top), h:Math.round(b.height), w:Math.round(b.width)}))(e.getBoundingClientRect()) : null;
  return JSON.stringify({
    vw: window.innerWidth, vh: window.innerHeight,
    scrollW: document.documentElement.scrollWidth,
    overflowX: document.documentElement.scrollWidth > window.innerWidth,
    canvas: box(c),
    sliderSection: box(sec),
    sliderVisible: !!r && r.getBoundingClientRect().top < window.innerHeight,
    mapBeforeFilters: (() => {
      if (!c) return null;
      const a = document.querySelector('aside');
      return a ? c.getBoundingClientRect().top < a.getBoundingClientRect().top : null;
    })(),
    labels: sec ? [...sec.querySelectorAll('button')].map(b => b.textContent.trim()) : []
  });
})()"""


def http(path, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main() -> int:
    xvfb = subprocess.Popen(["Xvfb", ":97", "-screen", "0", "1400x1000x24"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    chrome = subprocess.Popen(CHROME + ["about:blank"],
                              env={"DISPLAY": ":97", "PATH": "/usr/bin:/bin"},
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                http("/json/version")
                break
            except Exception:
                time.sleep(1)
        import websocket
        tab = http("/json/new?about:blank", method="PUT")
        conn = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=90)
        seq = [0]

        def send(m, p=None):
            seq[0] += 1
            mid = seq[0]
            conn.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
            while True:
                msg = json.loads(conn.recv())
                if msg.get("id") == mid:
                    return msg.get("result", {})

        send("Page.enable")
        send("Runtime.enable")
        send("Emulation.setDeviceMetricsOverride",
             {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
        send("Page.bringToFront")
        send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        send("Page.navigate", {"url": URL})
        time.sleep(18)
        r = send("Runtime.evaluate", {"expression": CHECK, "returnByValue": True})
        print(r.get("result", {}).get("value"))
        return 0
    finally:
        chrome.terminate()
        xvfb.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
