"""KvK (Handelsregister) company ingest — official register, API key required.

The KvK Dataservice is the authoritative list of Dutch companies per SBI code and
postcode area, but it is not free-and-open: a key is issued per developer account
(KVK_API_KEY). Without a key this channel reports that loudly instead of silently
producing nothing, because "0 companies from KvK" and "no key configured" must never
look the same.

Relevant SBI codes for a depot-charger census:
  49.41 goederenvervoer over de weg (carriers)
  49.42 verhuisbedrijven
  52.29 expediteurs / logistieke dienstverlening
  52.10 opslag en warehousing (shippers)
  53.10/53.20 post en koeriers
  38.32? no. 46.* wholesale shippers are too broad to start with.
"""
from __future__ import annotations

import json
import os

from ..fetch import Fetcher
from ..news_extract import normalize_company
from ..store import Store

API = "https://api.kvk.nl/api/v2/zoeken"
SBI_CODES = ("4941", "4942", "5229", "5210", "5310", "5320")


def run(store: Store, settings, sbi: str = "4941", page: int = 1) -> dict:
    key = os.environ.get("KVK_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "KVK_API_KEY is not set: register a free key at kvk.nl/dataservice and "
            "export KVK_API_KEY. This channel is skipped, not silently empty."
        )
    if sbi not in SBI_CODES:
        raise ValueError(f"sbi {sbi} not in {SBI_CODES}")
    url = f"{API}?sbiCode={sbi}&page={page}&size=100"
    with Fetcher(settings) as fetcher:
        resp = fetcher.get(url)
    if resp.status != 200:
        raise RuntimeError(f"kvk HTTP {resp.status}")
    payload = json.loads(resp.content)
    results = payload.get("resultaten") or []
    stored = 0
    with store.tx() as cur:
        for item in results:
            addr = item.get("adres") or {}
            name = (item.get("handelsnaam") or "").strip()
            if not name:
                continue
            cur.execute(
                """INSERT INTO companies(name, name_norm, kind, address, postcode, city,
                        geom, source, source_url, external_id, tags)
                   VALUES (%s,%s,'carrier',%s,%s,%s,
                        CASE WHEN %s IS NOT NULL AND %s IS NOT NULL
                             THEN ST_SetSRID(ST_MakePoint(%s,%s),4326) END,
                        'kvk', %s, %s, %s::jsonb)
                   ON CONFLICT (source, external_id) DO UPDATE
                      SET name = EXCLUDED.name, last_seen = now()""",
                (
                    name,
                    normalize_company(name),
                    " ".join(p for p in (addr.get("straat"), addr.get("huisnummer")) if p) or None,
                    (addr.get("postcode") or "").replace(" ", "") or None,
                    addr.get("plaats") or None,
                    item.get("lon"), item.get("lat"), item.get("lon"), item.get("lat"),
                    item.get("kvkNummer"),
                    f"https://www.kvk.nl/orderstraat/product-kiezen/?kvknummer={item.get('kvkNummer')}",
                    f"kvk/{item.get('vestigingsnummer') or item.get('kvkNummer')}",
                    json.dumps({"sbi": sbi, "type": item.get("type")})[:2000],
                ),
            )
            stored += 1
    return {"channel": "kvk", "sbi": sbi, "page": page, "results": len(results), "stored": stored}
