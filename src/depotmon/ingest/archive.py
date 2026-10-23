"""History harvest: publisher sitemaps -> keyword-slugged articles -> private-site signals.

The RSS channel only sees the last few weeks. Dutch trade press has years of depot
announcements sitting in its sitemaps, and those articles are the only lawful record of
private sites that were never permitted in a gemeenteblad (retrofit on existing premises).

Only publisher-owned sitemaps are read, the URL slug is used as the first filter so we
fetch a few hundred pages instead of tens of thousands, and every fetched page is cached
in raw_payloads with its publisher URL.
"""
from __future__ import annotations

import re
from datetime import date

from .. import news_extract as nx
from .. import textutils as tu
from ..fetch import Fetcher
from ..store import Store
from . import news

SITES: dict[str, str] = {
    "ttm": "https://www.ttm.nl/sitemap_index.xml",
    "nt": "https://www.nt.nl/sitemap.xml",
    "automotive": "https://www.automotive-online.nl/sitemap.xml",
    "wattisduurzaam": "https://www.wattisduurzaam.nl/sitemap_index.xml",
    "change": "https://www.change.inc/sitemap_index.xml",
    "schuttevaer": "https://www.schuttevaer.nl/sitemap.xml",
    # added 2026-10-22: live-probed 200 OK, publisher-owned sitemaps
    "logistiek": "https://www.logistiek.nl/sitemap.xml",
    "beurtvaartadres": "https://www.beurtvaartadres.nl/sitemap.xml",
    "binnenvaartkrant": "https://www.binnenvaartkrant.nl/sitemap.xml",
    "evupdate": "https://www.evupdate.nl/sitemap.xml",
    "transport": "https://www.transport-online.nl/wp-sitemap.xml",
    "duurzaam": "https://www.duurzaam-ondernemen.nl/sitemap_index.xml",
}

SLUG_RE = re.compile(
    r"laad|elektrisch|elektrific|zero-emissie|emissieloos|megawatt|snellad|oplaad|"
    r"waterstof|accu|batterij|charging|charging-hub",
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
OGTITLE_RE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', re.IGNORECASE)
LOC_RE = re.compile(
    r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)", re.IGNORECASE
)


def sitemap_urls(fetcher: Fetcher, index_url: str, max_sitemaps: int | None = None) -> tuple[list[str], list[str]]:
    """Return (article_urls, sitemaps_read) from a sitemap index or a plain urlset."""
    resp = fetcher.get(index_url)
    if resp.status != 200:
        raise RuntimeError(f"sitemap HTTP {resp.status}: {index_url}")
    locs = LOC_RE.findall(resp.text)
    reads = [index_url]
    if any(loc.endswith(".xml") for loc in locs) and not any("/20" in loc for loc in locs[:1]):
        children = [loc for loc in locs if loc.endswith(".xml")]
        if max_sitemaps:
            children = children[:max_sitemaps]
        urls: list[str] = []
        for child in children:
            r = fetcher.get(child)
            if r.status != 200:
                continue
            reads.append(child)
            urls.extend(LOC_RE.findall(r.text))
        return urls, reads
    return locs, reads


def page_title(html: str) -> str:
    m = OGTITLE_RE.search(html) or TITLE_RE.search(html)
    if not m:
        return ""
    title = tu.strip_html(m.group(1))
    # "Headline • TTM.nl" and "Headline | Nieuwsblad Transport" both leak the publisher
    # into the title, which then becomes a fake operator name downstream.
    title = re.split(r"\s*[|•·]\s*", title)[0]
    title = re.split(r"\s+[–-]\s+", title)[0]
    return title.strip().strip("‘’“”\"")[:200].strip()


def run(store: Store, settings, site: str = "ttm", max_articles: int = 150,
        max_sitemaps: int | None = None, offset: int = 0) -> dict:
    """Harvest one publisher's archive. Returns counts."""
    index_url = SITES[site]
    fetcher = Fetcher(settings)
    stats = {"site": site, "urls": 0, "candidates": 0, "fetched": 0, "kept": 0,
             "new": 0, "updated": 0, "unchanged": 0, "errors": 0, "sitemaps": 0}
    urls, reads = sitemap_urls(fetcher, index_url, max_sitemaps=max_sitemaps)
    stats["urls"] = len(urls)
    stats["sitemaps"] = len(reads)
    candidates = [u for u in urls if SLUG_RE.search(u)]
    # Skip pages we already hold: without this, a deeper run refetches the same
    # first N candidates and reports fetched=0, never reaching older history.
    with store.conn.cursor() as cur:
        cur.execute("SELECT url FROM raw_payloads WHERE url = ANY(%s)", (candidates,))
        known = {r["url"] for r in cur.fetchall()}
    candidates = [u for u in candidates if u not in known]
    stats["candidates"] = len(candidates)
    stats["known_skipped"] = len(known)
    for url in candidates[offset:offset + max_articles]:
        try:
            doc = fetcher.get(url)
            if doc.status != 200:
                stats["errors"] += 1
                continue
            stats["fetched"] += 1
            title = page_title(doc.text)
            body = news._clean_article(doc.text)
            sig = news.normalise_article(url, title, body, f"archive:{site}", None, site)
        except Exception:  # noqa: BLE001 — one bad article must not stop the harvest
            stats["errors"] += 1
            continue
        if sig is None:
            continue
        op = (sig.get("operator") or "")
        if "•" in op or len(op) > 60 or not news.plausible_operator(op):
            continue  # headline leaked into the operator field; not a company
        stats["kept"] += 1
        stats[store.upsert_signal(sig)] += 1
    store.record_health(f"archive:{site}", True, stats["fetched"])
    return stats
