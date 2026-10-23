"""Extraction helpers for trade-press articles about (private) depot charging.

Trade press is the *only* scalable, lawful channel that names private depot charging
sites: operators announce "wij hebben een eigen laadplein in gebruik genomen" long
before (or without) any permit being published. This module turns one article into
operator / place / power / point-count / lifecycle-intent fields.
"""

from __future__ import annotations

import re

from . import textutils as tu

SITE_WORDS = (
    "laadplein", "laadplekken", "laadplek", "laadinfrastructuur", "laadpunt",
    "laadpunten", "snellader", "snelladers", "snellaadplein", "laadstation",
    "laadstations", "laadstraat", "laadhub", "megawattlader", "mcs-lader",
    "laadoplossing", "laadvoorziening",
)

TRUCK_WORDS = (
    "vrachtwagen", "vrachtauto", "e-truck", "etruck", "eactros", "trekker",
    "oplegger", "transport", "logistiek", "distributie", "dc", "koel",
    "containervervoer", "wegvervoer", "zwaar transport", "zero-emissie",
    "zero emissie",
)

# Verbs that separate the company name from the predicate in a headline.
_VERBS = (
    "neemt", "opent", "kiest", "plaatst", "installeert", "realiseert", "bouwt",
    "start", "zoekt", "krijgt", "investeert", "richt", "lanceert", "test",
    "rijdt", "breidt", "verdubbelt", "maakt", "gebruikt", "laadt", "haalt",
    "zet", "wint", "ontvangt", "gaat", "wil", "heeft", "is", "doet", "rijdt",
    "verduurzaamt", "elektrificeert", "voegt", "levert", "bestelt", "besteedt",
    "strijden", "dingen", "winnen", "mogen", "gaan",
)

_VERB_RE = re.compile(r"\b(" + "|".join(_VERBS) + r")\b", re.IGNORECASE)

_STOP_HEAD = {"de", "het", "een", "dit", "dat", "nieuw", "nieuwe", "eerste", "twee",
              "drie", "vier", "vijf", "groei", "fors", "groot", "grootste", "extra"}

_PLACE_RE = re.compile(r"\bin\s+([A-Z][a-zà-ÿ]+(?:[- ][A-Z][a-zà-ÿ]+){0,2})\b")
_PLACE_NOT = {
    "nederland", "europa", "duitsland", "belgie", "belgië", "frankrijk",
    "belgisch", "belgische", "nederlandse", "vlaamse", "waalse",
    "januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
    "september", "oktober", "november", "december", "gebruik", "totaal",
    "samenwerking", "opdracht", "dienst", "binnenkort", "theorie", "praktijk",
    "de", "het", "beeld", "gesprek", "staat", "plaats", "deze", "die",
}

_POINTS_RE = re.compile(
    r"(\d{1,3})\s*(?:laadpunten|laadplekken|laadplaatsen|laadpalen|laders|"
    r"snelladers|laadstations|laadplekken)\b",
    re.IGNORECASE,
)

_AWARD_RE = re.compile(r"\b(award|prijs|onderscheiding|jury|nominatie)\b", re.IGNORECASE)
_SEARCH_RE = re.compile(
    r"\b(zoekt|zoeken|op zoek|gevraagd|beschikbaar stellen|wil een laadplek)\b",
    re.IGNORECASE,
)
_PLAN_RE = re.compile(
    r"\b(van plan|voornemen|onderzoekt|overweegt|ambitie|intentie|plan(nen)?\b)",
    re.IGNORECASE,
)
_CONSTRUCTION_RE = re.compile(
    r"\b(bouwt|realiseert|aanleg|in aanbouw|wordt gebouwd|werkzaamheden|"
    r"binnenkort|verwacht|oplever)",
    re.IGNORECASE,
)
_OPEN_RE = re.compile(
    r"\b(in gebruik genomen|geopend|operationeel|in bedrijf|rijdt sinds|"
    r"genomen|draait|actief|live)\b",
    re.IGNORECASE,
)


def has_site_intent(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in SITE_WORDS)


def is_truck_relevant(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in TRUCK_WORDS)


_ALLOWED_LOWER = {
    "van", "der", "de", "den", "het", "en", "te", "aan", "op", "bij", "voor", "met",
    "in", "of", "the", "of", "&", "bv", "b.v.", "nv", "n.v.", "holding", "group",
    "groep", "logistics", "logistiek", "transport", "international", "nederland",
}


POSTCODE_IN_NAME = re.compile(r"\b\d{4}\s?[A-Z]{2}\b")
REGION_PAREN = re.compile(
    r"\((?:[^)]*(?:noord|zuid|oost|west|amerika|europa|afrika|azi[ëe]|midden-oosten)[^)]*)\)",
    re.IGNORECASE,
)


_STOP_OPERATORS = {
    "na", "oude", "nieuwe", "de", "het", "een", "ook", "maar", "en", "dan",
    "dit", "dat", "deze", "er", "nu", "hier", "daar", "meer", "meest", "eerste",
    "tweede", "derde", "grootste", "eerste", "dit", "zijn", "wordt", "kan",
}

# A person's job title opening a name means a person, not a company. A headline
# about "Staatssecretaris Vivianne Heijnen" produced a map marker with no yard.
_TITLE_WORDS = {
    "staatssecretaris", "minister", "ministerie", "wethouder", "gedeputeerde",
    "burgemeester", "commissaris", "voorzitter", "directeur", "directrice",
    "ceo", "cfo", "cto", "coo", "manager", "professor", "prof", "dr", "ir",
    "mr", "drs", "koning", "koningin", "prins", "prinses", "raadslid",
    "woordvoerder", "bestuursvoorzitter", "oprichter", "eigenaar",
}

# Generic nouns that describe a type of place, never a specific company.
_GENERIC_NOUNS = {
    "tankstation", "tankstations", "laadpunt", "laadpunten", "laadplein",
    "laadplekken", "laadstation", "snellader", "snellaadstation", "depot",
    "distributiecentrum", "distributiecentra", "warehouse", "bedrijf",
    "bedrijven", "locatie", "locaties", "vestiging", "opslag", "terminal",
    "logistiek", "transport", "vervoerder", "wagenpark", "vrachtwagen",
    "bestelbus", "vrachtwagens", "laadinfrastructuur", "laadoplossing",
}

# Place names, not regions/continents/headline tails.
_REGION_WORDS = re.compile(
    r"^(?:noord|zuid|oost|west)?-?(?:amerika|europa|afrika|azi[\u00ebe]|"
    r"midden-oosten|nederland|benelux)$",
    re.IGNORECASE,
)


def plausible_city(city: str | None) -> bool:
    """A Dutch place name, not a leftover fragment like '?' or 'Noord-Amerika'."""
    if not city:
        return False
    c = city.strip(" -–—,.;:")
    if not (2 < len(c) <= 40):
        return False
    if any(ch in c for ch in "?:;!\u2018\u2019\"()"):
        return False
    if not c[0].isupper():
        return False
    if _REGION_WORDS.match(c):
        return False
    return True


def plausible_operator(name: str | None) -> bool:
    """A company name, not a headline fragment.

    Headline fragments ("Nederlands stel (Afrika)", "Changemaker X: 'In de
    toekomst...'") poison the premises score: they invent a site that does not
    exist and bury the real names. So: no sentence punctuation, at most six
    words, and every word capitalised unless it is a known connector.
    """
    if not name:
        return False
    s = name.strip()
    if not (2 < len(s) <= 55):
        return False
    if any(ch in s for ch in ":;!?\u2018\u2019\u201c\u201d\""):
        return False
    # "Na Total (Noord-Amerika)" and friends: a headline tail about a region,
    # not a company with a yard in the Netherlands.
    # An address is not a company. Almere permit templates yield operators like
    # "Laadplein Heerlenstraat 10 1324MG Almere"; each becomes its own company_key,
    # so these phantom sites never cluster or deduplicate.
    if POSTCODE_IN_NAME.search(s):
        return False
    if REGION_PAREN.search(s):
        return False
    words = s.split()
    if len(words) > 6:
        return False
    if len(words) == 1 and s.strip(".,").lower() in _STOP_OPERATORS:
        return False
    first = words[0].strip("()[],.").lower()
    if first in _TITLE_WORDS:
        return False
    low = {w.strip("()[],.").lower() for w in words}
    if len(words) <= 2 and low <= (_GENERIC_NOUNS | _ALLOWED_LOWER | {"de", "het", "een"}):
        return False
    for w in words:
        core = w.strip("()[],.")
        if not core or core.lower() in _ALLOWED_LOWER:
            continue
        if core[0].islower():
            return False
    return True


def extract_operator(title: str, body: str = "") -> str | None:
    """Company name from a headline such as 'Hartog & Bikker neemt ...'."""
    m = _VERB_RE.search(title)
    head = (title[: m.start()] if m else title).strip(" ,.;:-–—")
    head = re.sub(r"\s+(en|met|voor|aan|bij|in|op|van|nieuw|eigen)$", "", head,
                  flags=re.IGNORECASE).strip()
    # "Albert Heijn, SVZ en Tielbeke strijden om ..." -> take the first named company
    if "," in head:
        head = head.split(",")[0].strip()
    if plausible_operator(head) and head.lower() not in _STOP_HEAD:
        return head
    # fallback: legal-form or sector suffix anywhere in title/body
    m = re.search(
        r"([A-Z][\w&.'’\- ]{2,48}(?:B\.V\.|BV|N\.V\.|NV|Logistics|Logistiek|Transport|"
        r"Groep|Group|Distributie|DC))",
        title + " " + body[:800],
    )
    if m:
        cand = m.group(1).strip(" ,.;:-")
        if plausible_operator(cand):
            return cand
    return None


def extract_city(title: str, body: str = "") -> str | None:
    """First plausible place name introduced with 'in <Place>'."""
    for src in (title, body[:1200]):
        for m in _PLACE_RE.finditer(src):
            cand = m.group(1).strip()
            if cand.lower() in _PLACE_NOT:
                continue
            if not plausible_city(cand):
                continue
            return cand
    return None


def extract_points(text: str) -> int | None:
    m = _POINTS_RE.search(text)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    return n if 0 < n <= 200 else None


# Operators whose business *is* public charging: their "laadplein" is a public hub,
# i.e. the opposite of what this monitor looks for.
PUBLIC_OPERATORS = (
    "fastned", "milence", "ionity", "allego", "shell recharge", "shell", "vattenfall",
    "totalenergies", "total energies", "eneco", "engie", "evbox", "tesla", "supercharge",
    "laadpalen.nl", "greenflux", "newmotion", "e-flux", "electra", "zunder", "q-park",
    "bp pulse", "esso", "lidl", "aldi", "jumbo laadplein",
)

PUBLIC_CUES = (
    "openbaar", "publiek", "voor iedereen", "voor alle automobilisten", "alle merken",
    "laadhub", "trucklane", "truck lane", "snellaadplein", "openbare laadplein",
    "openbaar toegankelijk", "publiek toegankelijk", "zonder abonnement",
)

PRIVATE_CUES = (
    "eigen laadplein", "eigen laadinfrastructuur", "op eigen terrein", "eigen terrein",
    "op de vestiging", "bij het distributiecentrum", "op het dc", "eigen laadplek",
    "voor de eigen vloot", "eigen wagens", "eigen trucks", "eigen trekkers",
    "eigen chauffeurs", "besloten", "niet openbaar", "laadplein aangelegd",
    "laadinfrastructuur aangelegd", "voor eigen gebruik",
)

BE_CUES = ("belgië", "belgie", "belgisch", "vlaanderen", "vlaams", "wallonië",
           "wallonie", "antwerpen", "gent ", "brussel")


def access_hint(text: str, operator: str | None = None) -> str:
    """'public' | 'private' | 'unknown' — the core private-vs-public discriminator."""
    low = text.lower()
    op = (operator or "").lower()
    if any(cue in low for cue in PRIVATE_CUES):
        return "private"
    if any(name in op for name in PUBLIC_OPERATORS):
        return "public"
    if any(cue in low for cue in PUBLIC_CUES):
        return "public"
    return "unknown"


def country_hint(text: str) -> str:
    low = text.lower()
    return "BE" if any(cue in low for cue in BE_CUES) else "NL"


def lifecycle(text: str) -> str:
    """Coarse lifecycle state of the site described by the article."""
    if re.search(r"\b(strijden om|genomineerd|nominatie|shortlist)\b", text, re.I):
        return "nomination"
    if _AWARD_RE.search(text) and not _OPEN_RE.search(text):
        return "award"
    if _SEARCH_RE.search(text):
        return "search"
    if _OPEN_RE.search(text):
        return "opening"
    if _CONSTRUCTION_RE.search(text):
        return "construction"
    if _PLAN_RE.search(text):
        return "plan"
    return "unknown"


_STATUS = {
    "opening": "operational",
    "award": "operational",
    "nomination": "rumoured",
    "construction": "under_construction",
    "plan": "rumoured",
    "search": "rumoured",
    "unknown": "unknown",
}


def status_hint(text: str) -> str:
    return _STATUS.get(lifecycle(text), "unknown")


def private_claim(text: str) -> bool:
    """Does the text describe charging on the operator's own premises?"""
    low = text.lower()
    cues = ("eigen laadplein", "eigen laadinfrastructuur", "op eigen terrein",
            "eigen terrein", "op de vestiging", "bij het distributiecentrum",
            "op het dc", "eigen laadplek", "laadplein aangelegd", "eigen laadpunt",
            "niet openbaar", "besloten")
    return any(c in low for c in cues)


def power_kw(text: str) -> float | None:
    kw, _ = tu.extract_power_kw(text)
    if kw is None and "megawattlader" in text.lower():
        return 1000.0
    return kw


def normalize_company(name: str) -> str:
    """Key form used to merge the same operator across sources."""
    n = name.lower()
    n = re.sub(r"\b(b\.?v\.?|n\.?v\.?|v\.?o\.?f\.?|holding|group|groep|logistics|"
               r"logistiek|transport|international|nederland)\b", " ", n)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()
