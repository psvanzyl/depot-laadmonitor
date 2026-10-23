"""Ingest: trade press (RSS) — the discovery channel for *private* depot chargers.

Businesses announce their own charging hubs in sector media ("Hartog & Bikker neemt
twee e-trucks én een eigen laadplein in gebruik", Zwaag, Zoeterwoude, Zaandam...).
No register exists for these sites, so press announcements plus permit notices are the
only lawful, attributable evidence that a private premises has heavy-duty charging.

Only publisher-owned RSS feeds are used (no aggregators), each article is fetched once,
archived and cited back to the publisher.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from .. import news_extract as nx
from .. import textutils as tu
from ..config import Settings
from ..fetch import Fetcher
from ..store import Store, as_json

# Publisher-owned feeds (verified live 2026-10-22). Extend via NEWS_FEEDS.
# Every feed below was probed live (HTTP 200 + parseable + publisher-owned). Candidates
# that failed are recorded in SOURCES.md rather than silently retried every run.
DEFAULT_FEEDS = (
    "https://www.ttm.nl/feed/",
    "https://www.nieuwsbladtransport.nl/feed/",
    "https://www.automotive-online.nl/feed/",
    "https://www.tankpro.nl/feed/",
    "https://www.wattisduurzaam.nl/feed/",
    "https://www.change.inc/rss",
    "https://www.schuttevaer.nl/rss",
    "https://www.binnenvaartkrant.nl/feed/",
)

ATOM = "{http://www.w3.org/2005/Atom}"


def _text(item: ET.Element, *names: str) -> str:
    for name in names:
        el = item.find(name)
        if el is not None and el.text:
            return el.text.strip()
    return ""


def _link(item: ET.Element) -> str:
    link = _text(item, "link")
    if link:
        return link
    el = item.find(f"{ATOM}link")
    if el is not None:
        return el.get("href") or ""
    return ""


def _published(item: ET.Element) -> date | None:
    raw = _text(item, "pubDate", f"{ATOM}updated", f"{ATOM}published", "date")
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _clean_article(html: str) -> str:
    """Article body text, preferring <article> / <main> over the whole page."""
    for pattern in (r"(?is)<article[^>]*>(.*?)</article>",
                    r"(?is)<main[^>]*>(.*?)</main>"):
        m = re.search(pattern, html)
        if m and len(m.group(1)) > 500:
            return tu.strip_html(m.group(1))
    return tu.strip_html(html)


def normalise_article(url: str, title: str, body: str, feed: str,
                      published: date | None, publisher: str | None = None) -> dict | None:
    """Turn one article into a signal dict, or None when it is not a depot site."""
    haystack = f"{title}. {body}"
    if not nx.has_site_intent(haystack):
        return None
    if not (nx.is_truck_relevant(haystack) or (nx.power_kw(body) or 0) >= 350):
        return None
    operator = nx.extract_operator(title, body)
    city = nx.extract_city(title, body)
    if not operator:
        return None
    # A city-only signal is still useful, but never trusted as a located site.
    lifecycle = nx.lifecycle(haystack)
    access = nx.access_hint(haystack, operator)
    if access == "unknown" and nx.private_claim(haystack):
        access = "private"
    postcode = tu.extract_postcode(haystack)
    address = tu.extract_street_address(haystack)
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return {
        "signal_id": f"news:{key}",
        "source": "news",
        "source_key": key,
        "kind": "news",
        "name": tu.excerpt(title, 200),
        "operator": operator,
        "address": address,
        "postcode": postcode,
        "city": city,
        "lon": None,
        "lat": None,
        "bag_id": None,
        "power_kw": nx.power_kw(haystack),
        "n_points": nx.extract_points(haystack),
        "connector_types": None,
        "hgv_capable": nx.is_truck_relevant(haystack),
        "access": access,
        "status_hint": nx.status_hint(haystack),
        "text_excerpt": tu.excerpt(body, 700),
        "doc_url": url,
        "observed_at": published,
        "raw_state": {
            "feed": feed,
            "publisher": publisher,
            "lifecycle": lifecycle,
            "title": title,
            "private_claim": nx.private_claim(haystack),
            "geo_precision": "address" if postcode else ("city" if city else None),
            "country": nx.country_hint(haystack),
            "access": access,
            "company_key": nx.normalize_company(operator),
        },
        "content_hash": hashlib.sha256(
            f"{haystack}|{access}|{lifecycle}|{nx.country_hint(haystack)}|"
            f"{operator}|{city or ''}".encode("utf-8")
        ).hexdigest(),
    }


def run(store: Store, settings: Settings, feeds: list[str] | None = None) -> dict:
    feeds = feeds or list(settings.news_feeds)
    run_id = store.start_run("ingest", "news")
    stats = {"feeds": len(feeds), "items": 0, "keyword_hits": 0, "articles": 0,
             "kept": 0, "new": 0, "updated": 0, "unchanged": 0, "errors": []}
    try:
        with Fetcher(settings, store=store, run_id=run_id, source="news") as fetcher:
            for feed in feeds:
                try:
                    resp = fetcher.get(feed)
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    root = ET.fromstring(resp.content)
                except Exception as exc:
                    stats["errors"].append(f"{feed}: {exc}")
                    continue
                items = root.findall(".//item") or root.findall(f".//{ATOM}entry")
                stats["items"] += len(items)
                publisher = None
                chan = root.find("channel")
                if chan is not None:
                    publisher = _text(chan, "title")
                for item in items:
                    title = _text(item, "title", f"{ATOM}title")
                    if not title or not nx.has_site_intent(title):
                        continue
                    stats["keyword_hits"] += 1
                    url = _link(item)
                    if not url:
                        continue
                    try:
                        art = fetcher.get(url)
                        body = _clean_article(art.text) if art.status == 200 else ""
                    except Exception as exc:
                        stats["errors"].append(f"{url}: {exc}")
                        continue
                    stats["articles"] += 1
                    sig = normalise_article(url, title, body, feed,
                                            _published(item), publisher)
                    if sig is None:
                        continue
                    stats["kept"] += 1
                    stats[store.upsert_signal(sig)] += 1
        store.record_health("news", True, stats["kept"])
        ok = not stats["errors"] or stats["kept"] > 0
        store.finish_run(run_id, status="ok" if ok else "suspect",
                         records_in=stats["items"], records_out=stats["kept"],
                         note=as_json(stats))
    except Exception as exc:
        store.record_health("news", False, stats["kept"])
        store.finish_run(run_id, status="failed", records_in=stats["items"],
                         records_out=stats["kept"], error=str(exc))
        raise
    return stats
