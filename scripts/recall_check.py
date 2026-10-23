"""Ground-truth recall check: do we find private depots we KNOW exist?

The list below is deliberate: large Dutch private-fleet charging sites that any
credible monitor must know about. Whatever is missing here is the shopping list for
the company-first crawl — not a mystery, just work not done yet.

Run:  .venv/bin/python scripts/recall_check.py
"""
from __future__ import annotations

import sys

from depotmon.config import Settings
from depotmon.store import Store

# (search term, city hint) — city hint "" means any city
TARGETS: list[tuple[str, str]] = [
    ("Albert Heijn", "Zaandam"),
    ("Jumbo", "Veghel"),
    ("Picnic", "Amsterdam"),
    ("PostNL", ""),
    ("DHL", ""),
    ("GLS", ""),
    ("DPD", ""),
    ("Coolblue", ""),
    ("bol.com", "Waalwijk"),
    ("Action", ""),
    ("Lidl", ""),
    ("Aldi", ""),
    ("Sligro", "Veghel"),
    ("Bidfood", ""),
    ("Vos Logistics", ""),
    ("Peter Appel", "Ede"),
    ("Nabuurs", ""),
    ("Gubbels", ""),
    ("Cornelissen", ""),
    ("Van der Wal", ""),
    ("Breewel", ""),
    ("Heisterkamp", ""),
    ("Tielbeke", ""),
    ("Hartog & Bikker", "Zoeterwoude"),
    ("Oegema", "Apeldoorn"),
    ("Jan de Rijk", ""),
    ("Simon Loos", "Wognum"),
    ("HSF Logistics", ""),
    ("Neele-Vat", ""),
    ("Van Opzeeland", ""),
    ("Kuehne+Nagel", ""),
    ("DSV", ""),
    ("Rhenus", ""),
    ("Schenker", ""),
    ("UPS", ""),
    ("FedEx", ""),
    ("Milence", ""),
    ("Fastned", ""),
]


def main() -> int:
    settings = Settings()
    store = Store(settings.database_url)
    found = 0
    print(f"{'operator':22s} {'signal':28s} {'premises':26s} verdict")
    for name, city in TARGETS:
        with store.conn.cursor() as cur:
            cur.execute(
                """SELECT coalesce(left(operator,26),'-') AS op, city, status_hint AS hint
                     FROM signals
                    WHERE operator ILIKE %s AND (%s = '' OR city ILIKE %s)
                    ORDER BY last_seen DESC LIMIT 1""",
                (f"%{name}%", city, f"%{city}%"),
            )
            sig = cur.fetchone()
            cur.execute(
                """SELECT coalesce(left(operator,24),'-') AS op, city, tier, score
                     FROM premises_scores
                    WHERE operator ILIKE %s AND (%s = '' OR city ILIKE %s)
                    ORDER BY score DESC LIMIT 1""",
                (f"%{name}%", city, f"%{city}%"),
            )
            prem = cur.fetchone()
        if sig:
            sig = (sig["op"], sig["city"], sig["hint"])
        if prem:
            prem = (prem["op"], prem["city"], prem["tier"], prem["score"])
        verdict = "FOUND" if prem else ("signal-only" if sig else "MISSING")
        if prem:
            found += 1
        s_txt = f"{sig[0]} / {sig[1]} / {sig[2]}" if sig else "-"
        p_txt = f"{prem[0]} / {prem[1]} / {prem[2]} {float(prem[3]):.2f}" if prem else "-"
        print(f"{name:22s} {s_txt[:28]:28s} {p_txt[:26]:26s} {verdict}")
    print(f"\nrecall: {found}/{len(TARGETS)} known private depots")
    return 0 if found else 2


if __name__ == "__main__":
    sys.exit(main())
