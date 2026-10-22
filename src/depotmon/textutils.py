"""Pure text utilities for free-text permit notices.

No I/O here on purpose: every function is unit-tested in tests/test_textutils.py.
"""

from __future__ import annotations

import re
import unicodedata
from html import unescape

# --- keyword vocabularies --------------------------------------------------

CHARGE_KEYWORDS = [
    "laadinfrastructuur", "laadpaal", "laadpalen", "laadplein", "laadpleinen",
    "laadstation", "laadstations", "laadhub", "laadpunt", "laadpunten", "oplaadpunt",
    "snellader", "snelladers", "snellaad", "snellaadplein", "megawatt", "mcs",
    "elektrisch laden", "elektrische laad", "laadvoorziening", "laadvoorzieningen",
    "oplaadinfrastructuur", "charging hub", "truckcharging", "laadknooppunt",
]

TRUCK_KEYWORDS = [
    "vrachtwagen", "vrachtauto", "vrachtwagens", "vrachtauto's", "trekker",
    "oplegger", "zware bedrijfsvoertuigen", "zwaar bedrijfsvoertuig",
    "transportbedrijf", "transportonderneming", "distributiecentrum", "dc",
    "logistiek", "logistieke", "wegvervoer", "beroepsgoederenvervoer",
    "goederenvervoer", "koeloplegger", "containervervoer", "n3", "euro 6",
]

GRID_KEYWORDS = [
    "netaansluiting", "netverzwaring", "transportcapaciteit", "congestie",
    "middenspanningsstation", "hoogspanningsstation", "traforuimte", "trafo",
    "netcapaciteit", "inpassingsplan", "verzwaard", "onderstation",
]

CONSTRUCTION_KEYWORDS = [
    "in aanbouw", "bouw gestart", "start bouw", "realisatie", "wordt gebouwd",
    "werkzaamheden", "aanleg van", "gereed", "in gebruik genomen", "geopend",
]

POLICY_DOC_TYPES = (
    "omgevingsprogramma", "beleidsregel", "verordening", "nota", "visie",
    "structuurvisie", "kadernota", "uitvoeringsprogramma", "werkprogramma",
)

STATUS_VERBS = {
    "permit_granted": ["vergunning verleend", "verleend", "besluit tot verlening",
                       "vergunning is verleend", "verleende omgevingsvergunning"],
    "permit_requested": ["aangevraagd", "aanvraag omgevingsvergunning", "aanvraag",
                         "melding", "aanvraag om een omgevingsvergunning"],
    "rumoured": ["voornemen", "ontwerp", "concept", "verkenning", "ambitie",
                 "intentie", "planvorming"],
    "operational": ["in gebruik genomen", "geopend", "operationeel", "in bedrijf"],
    "under_construction": ["in aanbouw", "bouw gestart", "wordt gebouwd", "realisatie",
                           "werkzaamheden gestart"],
}

POWER_RE = re.compile(
    r"(?P<value>\d{1,5}(?:[.,]\d{1,3})?)\s*(?P<unit>MW|mW|kW|KW|kVA|KVA|MVA)\b"
)
KW_PER_MW = 1000.0
POSTCODE_RE = re.compile(r"\b(?P<pc>\d{4})\s?(?P<letters>[A-Z]{2})\b")
ADRES_RE = re.compile(
    r"(?P<street>[A-Z][A-Za-zÀ-ÿ0-9' .\-]{2,40}?)\s+(?P<num>\d{1,4})\s?(?P<add>[a-zA-Z]{0,2})\b"
)
KADASTER_RE = re.compile(
    r"(?:kadastraal|perceel|sectie)\s*[:\-]?\s*([A-Z]{1,2}\s*\d{1,6},?\s*(?:sectie\s*[A-Z])?)",
    re.IGNORECASE,
)


def strip_html(raw: str) -> str:
    """Turn (publication) markup into readable text."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[ \t\u00a0]+", " ", text).strip()


def compact(text: str) -> str:
    """Lowercased, punctuation-normalised form for keyword matching."""
    t = unicodedata.normalize("NFKD", text.lower())
    t = t.replace("’", "'").replace("ë", "e")
    return re.sub(r"\s+", " ", t)


def hits(text: str, vocabulary: list[str]) -> list[str]:
    t = compact(text)
    found = []
    for kw in vocabulary:
        kw_c = compact(kw)
        if len(kw_c) <= 3:
            # short tokens (mcs, n3, dc, eu) must match as whole words
            if re.search(rf"\b{re.escape(kw_c)}\b", t):
                found.append(kw)
        elif kw_c in t:
            found.append(kw)
    return sorted(set(found))


def extract_power_kw(text: str) -> tuple[float | None, str | None]:
    """Largest plausible power mention in kW. Ignores values < 10 kW."""
    best: tuple[float, str] | None = None
    for m in POWER_RE.finditer(text):
        raw_value = m.group("value")
        if "," in raw_value:
            value = float(raw_value.replace(".", "").replace(",", "."))
        elif "." in raw_value:
            head, _, frac = raw_value.partition(".")
            # "3.600" is Dutch thousands notation; "1.5" is a decimal
            value = float(head + frac) if len(frac) == 3 else float(raw_value)
        else:
            value = float(raw_value)
        unit = m.group("unit").upper()
        kw = value * KW_PER_MW if unit in {"MW", "MVA"} else value
        if kw < 10 or kw > 500_000:
            continue
        if best is None or kw > best[0]:
            best = (kw, m.group(0))
    if best is None:
        return None, None
    return best[0], best[1]


def extract_postcode(text: str) -> str | None:
    m = POSTCODE_RE.search(text)
    if not m:
        return None
    return f"{m.group('pc')}{m.group('letters')}"


STREET_SUFFIX = (
    "straat", "laan", "weg", "plein", "kade", "dijk", "hof", "gracht", "singel",
    "pad", "baan", "erf", "steeg", "markt", "ring", "zoom", "dreef", "haven",
    "park", "rotonde", "boulevard", "promenade",
)

STREET_ADDR_RE = re.compile(
    r"\b([A-Z][\w'\-]*(?:" + "|".join(STREET_SUFFIX) + r"))\s+(\d{1,4}[a-zA-Z]?)\b"
)


def extract_street_address(text: str) -> str | None:
    """Conservative 'Straatnaam 12' extractor for prose (news), suffix-anchored.

    News prose is full of numbers ("eActros 600", "3.600 kW"); only accept a match when
    the token carries a real Dutch street suffix, so we never invent an address.
    """
    m = STREET_ADDR_RE.search(text)
    if not m:
        return None
    street = m.group(1).strip(" ,.;:-")
    if len(street) < 5:
        return None
    return f"{street} {m.group(2)}"


def extract_address(text: str, postcode: str | None = None) -> str | None:
    """Return a geocodable 'street number, postcode city' style address."""
    if postcode:
        pc = POSTCODE_RE.search(text)
        if pc:
            tail = text[pc.end(): pc.end() + 60]
            city = re.split(r"[^A-Za-zÀ-ÿ' \-]", tail.strip())[0].strip()
            head = text[max(0, pc.start() - 80): pc.start()]
            for m in ADRES_RE.finditer(head):
                street = m.group("street").strip(" ,.;:-")
                if len(street) >= 3:
                    num = m.group("num") + (m.group("add") or "")
                    return f"{street} {num}, {postcode}" + (f" {city}" if city else "")
    m = ADRES_RE.search(text)
    if m:
        street = m.group("street").strip(" ,.;:-")
        num = m.group("num") + (m.group("add") or "")
        if len(street) >= 3 and not re.match(r"^(op|de|het|een|van|per)\b", street.lower()):
            return f"{street} {num}"
    return None


def extract_applicant(text: str) -> str | None:
    """Best-effort applicant name from a permit notice.

    Handles "Aanvrager: X", "Aanvraag ontvangen van X", "ingediend door X",
    case-insensitively, and stops at sentence boundaries while keeping
    abbreviation dots such as "B.V." / "N.V." intact.
    """
    patterns = [
        r"(?:aanvrager|naam aanvrager|initiatiefnemer|aanvragende partij)\s*[:\-]\s*"
        r"([A-Z0-9][^\n]{2,110})",
        r"(?:aanvraag|verzoek|melding)[^.\n]{0,90}?(?:ontvangen van|ingediend door|"
        r"afkomstig van)\s+([A-Z0-9][^\n]{2,110})",
        r"(?:ontvangen van|ingediend door|gedaan door|namens)\s+([A-Z0-9][^\n]{2,90})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        raw = re.split(
            r"\s+(?:voor|om|betreffende|met betrekking|op het perceel|op locatie|"
            r"ten behoeve|inzake)\b",
            m.group(1),
            maxsplit=1,
        )[0]
        # cut at a sentence end, keeping abbreviation dots like "B.V."
        cut = re.split(r"(?<=\.)\s+(?=[a-z])", raw, maxsplit=1)[0]
        cand = cut.strip(" ,;:")
        if 2 < len(cand) < 120:
            return cand
    return None


def extract_kadaster(text: str) -> str | None:
    m = KADASTER_RE.search(text)
    return m.group(1).strip() if m else None


def status_from_text(text: str, doc_type: str | None = None) -> str:
    """Best-effort status of the *site* described by the notice."""
    t = compact(text)
    dtype = compact(doc_type or "")
    if any(w in dtype for w in POLICY_DOC_TYPES):
        return "rumoured"
    for status in ("operational", "permit_granted", "under_construction",
                   "permit_requested", "rumoured"):
        for phrase in STATUS_VERBS[status]:
            if compact(phrase) in t:
                return status
    return "unknown"


def is_relevant(text: str) -> tuple[bool, dict[str, list[str]]]:
    """Gate: does this notice describe a truck-depot charging site?"""
    charge = hits(text, CHARGE_KEYWORDS)
    truck = hits(text, TRUCK_KEYWORDS)
    grid = hits(text, GRID_KEYWORDS)
    power_kw, _ = extract_power_kw(text)
    relevant = bool(charge) and (bool(truck) or (power_kw or 0) >= 350)
    return relevant, {"charge": charge, "truck": truck, "grid": grid}


def excerpt(text: str, limit: int = 400) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return t[:limit]
