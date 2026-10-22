"""Ingest: Officiële Bekendmakingen via SRU 2.0 (repository.overheid.nl).

Flow: SRU search (keyword + dt.modified window) -> per-record metadata -> fetch the
publication XML for the full text -> keyword gate -> normalise to a Signal.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any

from .. import textutils as tu
from ..config import Settings
from ..fetch import Fetcher
from ..store import Store, as_json

SRU_URL = "https://repository.overheid.nl/sru"

SEARCH_TERMS = (
    "laadplein laadplekken laadinfrastructuur laadstation snellader megawattlader "
    "vrachtwagenlaadplein laadhub"
)

# A charging site is only evidence when the publication is about a decision/permit.
PERMIT_SEMANTICS = re.compile(
    r"omgevingsvergunning|aanvraag|besluit|melding|vergunning|ontwerpbesluit|kennisgeving",
    re.IGNORECASE,
)
CITY_STOP = {"nederland", "nederlandse", "deze", "gemeente", "provincie", "noorwegen"}

# Permit language for a charging site. Deliberately generous: the SRU query already
# matched, so a miss here is pure recall loss.
CHARGE_RE = re.compile(
    r"laadplein|laadplekken|laadinfrastructuur|laadstation|laadpaal|laadpunt|laadplaats|"
    r"snellader|snellaad|megawattlad|laadhub|oplaadpunt|oplaadstation|"
    r"elektrische?\s+(?:vrachtauto|vrachtwagen|truck|bestelbus|voertuigen)",
    re.IGNORECASE,
)


def plausible_company(cand: str | None) -> str | None:
    """Reject sentence fragments posing as an applicant (the old failure mode)."""
    if not cand:
        return None
    cand = cand.strip(" ,.;:")
    # "De Vries Transport B.V. Locatie: Zonnebaan 12" -> "De Vries Transport B.V."
    cand = re.split(
        r"\s+(?:locatie|adres|perceel|op locatie|het betreft|zaaknummer|kenmerk|project)\b",
        cand, maxsplit=1, flags=re.IGNORECASE,
    )[0].strip(" ,.;:")
    if not cand or len(cand) > 60:
        return None
    # A sentence ended inside the candidate ("werd gedaan. In de ..."); "B.V." stays legal.
    if re.search(r"[a-zà-ÿ]{3,}\.\s+[A-Z]", cand):
        return None
    if not cand[0].isupper() or len(cand.split()) > 8:
        return None
    if any(w in cand.lower() for w in (" de waterbeheerder", " het college", " burgemeester")):
        return None
    return cand


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_of(elem: ET.Element) -> str:
    return " ".join("".join(elem.itertext()).split())


def _collect(record: ET.Element) -> dict[str, Any]:
    """Flatten a record subtree into {localname: [texts]} (first value wins)."""
    out: dict[str, list[str]] = {}
    for child in record.iter():
        name = _localname(child.tag)
        txt = _text_of(child)
        if txt:
            out.setdefault(name, []).append(txt)
    return out


def build_query(since: date) -> str:
    return (
        'c.product-area=="officielepublicaties"'
        f' AND cql.textAndIndexes any "{SEARCH_TERMS}"'
        f" AND dt.modified>={since.isoformat()}"
    )


def search(fetcher: Fetcher, settings: Settings, since: date,
           max_records: int | None = None) -> tuple[list[dict], int]:
    """Return (records, total_available)."""
    limit = max_records or settings.permit_max_records
    records: list[dict] = []
    total = 0
    start = 1
    page = 100
    while len(records) < limit:
        resp = fetcher.get(
            SRU_URL,
            params={
                "query": build_query(since),
                "maximumRecords": min(page, limit - len(records)),
                "startRecord": start,
            },
        )
        if resp.status != 200:
            raise RuntimeError(f"SRU HTTP {resp.status}")
        root = ET.fromstring(resp.content)
        total_txt = next(
            (t for t in (_text_of(e) for e in root.iter() if _localname(e.tag) == "numberOfRecords")),
            "0",
        )
        try:
            total = int(total_txt)
        except ValueError:
            total = 0
        page_records = [e for e in root.iter() if _localname(e.tag) == "record"]
        for rec in page_records:
            fields = _collect(rec)
            flat = {k: v[0] for k, v in fields.items()}
            xml_url = None
            for e in rec.iter():
                if _localname(e.tag) == "itemUrl" and e.get("manifestation") == "xml":
                    xml_url = _text_of(e)
                    break
            records.append({"fields": flat, "xml_url": xml_url})
        if len(page_records) == 0 or start + len(page_records) > total:
            break
        start += len(page_records)
    return records[:limit], total


DOC_TYPE_NOISE = re.compile(
    r"(beleidsregel|verordening|omgevingsprogramma|nota|visie|werkprogramma|"
    r"uitvoeringsprogramma|kadernota|structuurvisie)",
    re.IGNORECASE,
)


def normalise_document(rec: dict, fetched_text: str | None,
                       doc_url: str | None) -> dict | None:
    """Turn one SRU record (+ optional full text) into a signal dict, or None."""
    f = rec["fields"]
    title = f.get("title") or f.get("titel") or ""
    doc_type = f.get("type") or f.get("owmskern:type") or ""
    identifier = f.get("identifier") or f.get("preferredUrl") or doc_url or ""
    modified = f.get("modified") or f.get("available") or ""

    body = fetched_text or title
    relevant, kw = tu.is_relevant(body)
    if not relevant and fetched_text is not None:
        # title-only noise (policy docs) is dropped unless the body qualified
        return None
    if not relevant:
        return None

    if not CHARGE_RE.search(body):
        return None
    _pw = tu.extract_power_kw(body)[0]
    if not (kw["truck"] or (_pw or 0) >= 350
            or re.search(r"laadplein|laadstation|laadinfrastructuur|laadplekken",
                         body, re.IGNORECASE)):
        return None
    if not PERMIT_SEMANTICS.search(title) and not PERMIT_SEMANTICS.search(body[:400]):
        return None
    # A verkeersbesluit is about the public road (reserved parking bays), never a
    # depot behind a gate — the opposite of what this monitor collects.
    if re.search(r"verkeersbesluit", f"{title} {body[:300]}", re.IGNORECASE):
        return None

    postcode = tu.extract_postcode(body)
    address = tu.extract_address(body, postcode)
    if address:
        address = re.sub(r"^(?:met als adres|op locatie|locatie|adres)\s*:?\s*", "",
                         address, flags=re.IGNORECASE).strip(" ,.;:")
        # a PO box is the objections desk, not the site
        if re.search(r"postbus|antwoordnummer", address, re.IGNORECASE):
            address = None
    city = None
    m = re.search(r"\b\d{4}\s?[A-Z]{2}\s+([A-Z][a-zà-ÿ'\-]{2,})", body)
    if m and m.group(1).lower() not in CITY_STOP:
        city = m.group(1)
    else:
        m = re.search(r"\bin\s+([A-Z][a-zà-ÿ'\-]{2,})\s*[,.]", body)
        if m and m.group(1).lower() not in CITY_STOP:
            city = m.group(1)
    if address is None and postcode is None:
        return None
    power_kw, power_raw = tu.extract_power_kw(body)
    applicant = plausible_company(tu.extract_applicant(body))
    status = tu.status_from_text(body, doc_type)
    if status == "rumoured" and DOC_TYPE_NOISE.search(doc_type or title):
        # policy documents are not sites; only keep them if they carry an address
        if not address:
            return None
    if address is None and identifier is None:
        return None

    key = hashlib.sha1((identifier or title).encode("utf-8")).hexdigest()[:16]
    observed = None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", modified or "")
    if m:
        observed = m.group(1)

    return {
        "signal_id": f"permits:{key}",
        "source": "permits",
        "source_key": key,
        "kind": "permit",
        "name": tu.excerpt(title, 200),
        "operator": applicant,
        "address": address,
        "postcode": postcode,
        "city": city,
        "lon": None,
        "lat": None,
        "bag_id": None,
        "power_kw": power_kw,
        "n_points": None,
        "connector_types": None,
        "hgv_capable": bool(kw["truck"]) or (power_kw or 0) >= 350,
        "access": "private" if "niet openbaar" in tu.compact(body) else None,
        "status_hint": status,
        "text_excerpt": tu.excerpt(body, 600),
        "doc_url": doc_url,
        "observed_at": observed,
        "raw_state": {
            "title": title,
            "doc_type": doc_type,
            "identifier": identifier,
            "modified": modified,
            "content_area": f.get("content-area"),
            "keywords": kw,
            "power_raw": power_raw,
            "kadaster": tu.extract_kadaster(body),
            "publisher": f.get("creator"),
            "has_full_text": fetched_text is not None,
        },
        "content_hash": hashlib.sha256(
            json.dumps(
                [identifier, status, applicant, address, postcode, city, power_kw,
                 bool(kw["truck"]), kw["charge"]],
                sort_keys=True,
            ).encode()
        ).hexdigest(),
    }


SRU_EXEMPT = {
    "repository.overheid.nl": (
        "KOOP SRU 2.0 is a documented reuse API (Handleiding SRU 2.0, v1.4). The host "
        "serves a blanket 'Disallow: /' to crawlers; we query the API only, at most "
        "8 keywords per run, one request each, with an identifying User-Agent."
    )
}


def run(store: Store, settings: Settings, *, days: int | None = None,
        fetch_docs: bool | None = None) -> dict:
    """Ingest permit notices modified in the last `days` days."""
    lookback = days or settings.permit_lookback_days
    fetch_docs = settings.permit_fetch_docs if fetch_docs is None else fetch_docs
    since = date.today() - timedelta(days=lookback)
    run_id = store.start_run("ingest", "permits")
    stats = {"records": 0, "relevant": 0, "new": 0, "updated": 0, "unchanged": 0,
             "docs_fetched": 0, "skipped": 0}
    try:
        with Fetcher(
            settings, store=store, run_id=run_id, source="permits",
            robots_exempt=SRU_EXEMPT if settings.sru_enabled else None,
        ) as fetcher:
            records, total = search(fetcher, settings, since)
            stats["records"] = len(records)
            stats["total_available"] = total
            for rec in records:
                body = None
                if fetch_docs and rec["xml_url"]:
                    try:
                        doc = fetcher.get(rec["xml_url"])
                        if doc.status == 200:
                            body = tu.strip_html(doc.text)
                            stats["docs_fetched"] += 1
                    except Exception:  # a single document must not kill the run
                        stats["skipped"] += 1
                sig = normalise_document(rec, body, rec["xml_url"])
                if sig is None:
                    stats["skipped"] += 1
                    continue
                stats["relevant"] += 1
                stats[store.upsert_signal(sig)] += 1
        store.record_health("permits", True, stats["records"])
        store.finish_run(run_id, status="ok", records_in=stats["records"],
                         records_out=stats["relevant"], note=as_json(stats))
        return stats
    except Exception as exc:
        store.record_health("permits", False, stats.get("records", 0), error=str(exc)[:500])
        store.alert("source_failure", f"permits ingest failed: {exc}", source="permits")
        store.finish_run(run_id, status="failed", error=str(exc)[:2000])
        raise
