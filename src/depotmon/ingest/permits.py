"""Ingest: Officiële Bekendmakingen via SRU 2.0 (repository.overheid.nl).

Flow: SRU search (keyword + dt.modified window) -> per-record metadata -> fetch the
publication XML for the full text -> keyword gate -> normalise to a Signal.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
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
    "vrachtwagenlaadplein laadhub laadvoorziening laadplaats laadpunt oplaadstation"
)

# Gemeenteblad, Staatscourant, Provinciaal blad, Waterschapsblad carry site decisions.
# kst/blg/hnd/stb are Kamerstukken, bijlagen, handelingen and tractaten: ~half of every
# hit list, and structurally incapable of naming a private depot.
PUB_TYPE_RE = re.compile(r"^(gmb|stc|prb|wsb)-", re.IGNORECASE)

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


# "V E R K E E R S B E S L U I T" is still a verkeersbesluit — a decision about the
# public road, i.e. the opposite of a depot behind a gate.
_SPACED_CAPS = re.compile(r"(?<=\b[A-Z])\s+(?=[A-Z]\b)")
PUBLIC_ROAD_RE = re.compile(
    r"verkeersbesluit|reserveren van parkeerplaats|parkeerplaatsen voor (?:het )?opladen|"
    r"parkeren voor het opladen|openbare laadplaats|blauwe zone",
    re.IGNORECASE,
)

# street + number, optional comma, postcode, place — anchored on the postcode, which is
# the only part of a Dutch address that is unambiguous in prose.
SITE_RE = re.compile(
    r"([A-Z][\w\.\-']*(?:\s+[A-Za-z0-9\.\-']+){0,4}\s+\d{1,4}[a-zA-Z]?)"
    r"\s*,?\s*(\d{4}\s?[A-Z]{2})\s+([A-Z][a-z\u00e0-\u00ff\-]{2,})"
)
ADDR_NOISE = re.compile(
    r"\s+(?:het college|burgemeester|inge?komen|kenmerk|gemeente|aangevraagde|kennisgeving|"
    r"ontwerp|besluit|zaaknummer|postbus|namens|de aanvraag|hierbij)\b.*$",
    re.IGNORECASE,
)


def tidy_caps(text: str) -> str:
    """Collapse spaced-out capitals so keyword checks survive typesetting games."""
    return _SPACED_CAPS.sub("", text)


def site_address(body: str) -> tuple[str | None, str | None, str | None]:
    """(address, postcode, city) for the first plausible site address in the body."""
    for m in SITE_RE.finditer(body):
        street, pc, place = m.group(1), m.group(2).replace(" ", ""), m.group(3)
        # a PO box is the objections desk, not the site
        before = body[max(0, m.start() - 40):m.start()].lower()
        if "postbus" in before or "antwoordnummer" in before:
            continue
        street = street.strip(" ,;:")
        # "Maastricht Rekoutweg 14" -> "Rekoutweg 14" (the place was swallowed)
        head = street.split()
        if place and len(head) > 1 and head[0].lower() == place.lower():
            street = " ".join(head[1:])
        street = re.sub(r"^(?:aan de|aan|op de|op|locatie|adres)\s+", "", street, flags=re.I)
        # administrative addresses are the desk that handles objections, not the depot
        if re.match(r"(postbus|antwoordnummer|t\.?a\.?v\.?|commissie|afdeling|team|p/a)\b",
                    street, re.IGNORECASE):
            continue
        if not re.match(r"[A-Z]", street) or len(street) > 60:
            continue
        return street, pc, place
    return None, None, None


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


def build_query(since: date, until: date | None = None) -> str:
    q = (
        'c.product-area=="officielepublicaties"'
        f' AND cql.textAndIndexes any "{SEARCH_TERMS}"'
        f" AND dt.modified>={since.isoformat()}"
    )
    # SRU refuses to page past startRecord=10000, so long backfills must be cut into
    # windows (a month of charging hits is well under a thousand records).
    if until:
        q += f" AND dt.modified<{until.isoformat()}"
    return q


def search(fetcher: Fetcher, settings: Settings, since: date,
           max_records: int | None = None,
           until: date | None = None) -> tuple[list[dict], int]:
    """Return (records, total_available)."""
    limit = max_records or settings.permit_max_records
    records: list[dict] = []
    total = 0
    start = 1
    page = 100
    while len(records) < limit:
        # SRU retry: KOOP answers 500/504 under load or past deep startRecord.
        # Retry with a growing pause before giving up on the whole month window.
        resp = None
        for attempt in range(1, 4):
            resp = fetcher.get(
                SRU_URL,
                params={
                    "query": build_query(since, until),
                    "maximumRecords": min(page, limit - len(records)),
                    "startRecord": start,
                },
            )
            if resp.status == 200:
                break
            if resp.status in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(f"SRU HTTP {resp.status} (startRecord={start})")
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
            ident = flat.get("identifier") or ""
            if not PUB_TYPE_RE.match(ident):
                continue
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



def worth_fetching(rec: dict) -> bool:
    """Metadata is a reliable summary for gmb/stc/prb/wsb notices — use it before fetching.

    The 24-month backfill is ~10k records; fetching every XML would be both slow and rude,
    so a document is fetched only when its title/abstract could plausibly describe a depot.
    Errs towards fetching: no abstract means we cannot judge, so we look.
    """
    f = rec.get("fields") or {}
    meta = tidy_caps(f"{f.get('title') or ''} {f.get('abstract') or ''}")
    if not meta.strip():
        return False
    if PUBLIC_ROAD_RE.search(meta):
        return False
    if DOC_TYPE_NOISE.search(meta):
        return False
    if CHARGE_RE.search(meta):
        return True
    if not (f.get("abstract") or "").strip():
        # title-only metadata: a permit word is reason enough to look at the body
        return bool(PERMIT_SEMANTICS.search(meta))
    return False


def normalise_document(rec: dict, fetched_text: str | None,
                       doc_url: str | None) -> dict | None:
    """Turn one SRU record (+ optional full text) into a signal dict, or None."""
    f = rec["fields"]
    title = f.get("title") or f.get("titel") or ""
    doc_type = f.get("type") or f.get("owmskern:type") or ""
    identifier = f.get("identifier") or f.get("preferredUrl") or doc_url or ""
    modified = f.get("modified") or f.get("available") or ""

    body = fetched_text or title
    # Hints only: the shared is_relevant() vocabulary is narrower than permit language,
    # so its verdict must never gate this channel (it silently dropped 157 of 160).
    _, kw = tu.is_relevant(body)
    # Policy programmes are 100k+ characters of prose, never a site notice.
    if len(body) > 60_000:
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
    if PUBLIC_ROAD_RE.search(tidy_caps(f"{title} {body[:400]}")):
        return None

    address, postcode, city = site_address(body)
    if address is None:
        # An address we cannot anchor to a postcode is worse than no address: the
        # postcode alone still identifies the premises.
        postcode = tu.extract_postcode(body)
        if postcode:
            m = re.search(rf"{postcode[:4]}\s?{postcode[4:]}", body)
            if m:
                head = body[max(0, m.start() - 60):m.start()].lower()
                if "postbus" in head or "antwoordnummer" in head:
                    postcode = None

    if address is None and postcode is None:
        return None
    power_kw, power_raw = tu.extract_power_kw(body)
    applicant = plausible_company(tu.extract_applicant(body))
    status = tu.status_from_text(body, doc_type)
    if re.search(r"ontwerpbesluit|ontwerp-?besluit|voorgenomen besluit", tidy_caps(title),
                 re.IGNORECASE):
        status = "rumoured"
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
        fetch_docs: bool | None = None, max_records: int | None = None,
        since: date | None = None, until: date | None = None) -> dict:
    """Ingest permit notices modified in [since, until) — or the last `days` days."""
    lookback = days or settings.permit_lookback_days
    fetch_docs = settings.permit_fetch_docs if fetch_docs is None else fetch_docs
    since = since or date.today() - timedelta(days=lookback)
    run_id = store.start_run("ingest", "permits")
    stats = {"records": 0, "relevant": 0, "new": 0, "updated": 0, "unchanged": 0,
             "docs_fetched": 0, "skipped": 0, "prescreened": 0}
    try:
        with Fetcher(
            settings, store=store, run_id=run_id, source="permits",
            robots_exempt=SRU_EXEMPT if settings.sru_enabled else None,
        ) as fetcher:
            records, total = search(fetcher, settings, since,
                                    max_records=max_records, until=until)
            stats["records"] = len(records)
            stats["total_available"] = total
            for rec in records:
                body = None
                if fetch_docs and rec["xml_url"] and not worth_fetching(rec):
                    stats["prescreened"] = stats.get("prescreened", 0) + 1
                    continue
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
