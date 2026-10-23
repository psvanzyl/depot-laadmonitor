"""Polite HTTP client: robots.txt, rate limits, retries, content-addressed archive.

Every response body is hashed (SHA-256) and archived (gzip) before it is parsed;
the parser is handed the *archived* bytes so a re-run can be replayed offline.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class RobotsDisallowed(RuntimeError):
    pass


@dataclass
class FetchResult:
    url: str
    status: int
    content: bytes
    fetched_at: datetime
    payload_hash: str
    storage_path: str | None
    content_type: str | None

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        import json

        return json.loads(self.content)


def _maybe_gunzip(body: bytes, content_type: str | None) -> bytes:
    """Decompress a gzip-encoded body.

    Some publishers serve a gzip FILE under a .gz URL (e.g. the NDW open-data
    dump). httpx only transparently decompresses when the server sets
    Content-Encoding: gzip; a .gz file served as application/octet-stream
    arrives still compressed, and parsing it as UTF-8 JSON blows up with
    "'utf-8' codec can't decode byte 0x8b" (0x1f 0x8b is the gzip magic).
    """
    if body[:2] != b"\x1f\x8b":
        return body
    try:
        return gzip.decompress(body)
    except OSError:
        return body


class Fetcher:
    """Rate-limited, retrying, archiving HTTP client."""

    def __init__(self, settings: Settings, store=None, run_id: int | None = None,
                 source: str = "", robots_exempt: dict[str, str] | None = None):
        # ``robots_exempt`` maps host -> written reason. Used ONLY for documented
        # machine interfaces that blanket-disallow crawlers (KOOP SRU). Every use is
        # logged and stored with the ingest run, so the decision stays auditable.
        self.robots_exempt = robots_exempt or {}
        self.settings = settings
        self.store = store
        self.run_id = run_id
        self.source = source
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._exempt_logged: set[str] = set()
        self._client = httpx.Client(
            headers={"User-Agent": settings.user_agent, "Accept-Language": "nl,en;q=0.7"},
            timeout=settings.http_timeout,
            follow_redirects=True,
        )

    # -- context manager ---------------------------------------------------
    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc) -> None:
        self._client.close()

    # -- robots ------------------------------------------------------------
    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        host = urlparse(url).netloc
        if host in self._robots:
            return self._robots[host]
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"https://{host}/robots.txt")
        try:
            resp = self._client.get(f"https://{host}/robots.txt")
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp = None
        except httpx.HTTPError:
            rp = None
        self._robots[host] = rp
        return rp

    def _check_robots(self, url: str) -> None:
        if not self.settings.respect_robots:
            return
        host = urlparse(url).netloc
        if host in self.robots_exempt:
            if host not in self._exempt_logged:
                self._exempt_logged.add(host)
                logger.info(
                    "robots exemption for %s: %s", host, self.robots_exempt[host]
                )
            return
        rp = self._robots_for(url)
        if rp is None:
            return
        if not rp.can_fetch(self.settings.user_agent, url):
            raise RobotsDisallowed(f"robots.txt disallows {url} for our UA")

    # -- throttling --------------------------------------------------------
    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        interval = (
            self.settings.overpass_interval
            if "overpass" in host
            else self.settings.min_interval
        )
        last = self._last.get(host)
        now = time.monotonic()
        if last is not None:
            wait = interval - (now - last)
            if wait > 0:
                time.sleep(wait)
        self._last[host] = time.monotonic()

    # -- archive -----------------------------------------------------------
    def _archive(self, content: bytes, url: str, ctype: str | None) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        suffix = "json"
        if "xml" in (ctype or ""):
            suffix = "xml"
        elif "html" in (ctype or ""):
            suffix = "html"
        elif url.endswith(".gz"):
            suffix = "gz"
        rel = Path(self.source or "misc") / day / digest[:2] / f"{digest}.{suffix}.gz"
        target = self.settings.raw_dir / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            with gzip.open(tmp, "wb") as fh:
                fh.write(content)
            tmp.replace(target)
        return digest, str(rel)

    # -- main --------------------------------------------------------------
    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        method: str = "GET",
        data: dict | None = None,
    ) -> FetchResult:
        self._check_robots(url)
        last_err: Exception | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            self._throttle(url)
            try:
                resp = self._client.request(
                    method, url, params=params, headers=headers, data=data
                )
                if resp.status_code in {429, 500, 502, 503, 504} and attempt < self.settings.max_retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                ctype = resp.headers.get("content-type")
                body = _maybe_gunzip(resp.content, ctype)
                digest, rel = self._archive(body, url, ctype)
                if self.store is not None:
                    self.store.record_raw(
                        payload_hash=digest,
                        source=self.source,
                        url=str(resp.request.url),
                        run_id=self.run_id,
                        content_type=ctype,
                        http_status=resp.status_code,
                        n_bytes=len(body),
                        storage_path=rel,
                    )
                return FetchResult(
                    url=str(resp.request.url),
                    status=resp.status_code,
                    content=body,
                    fetched_at=datetime.now(timezone.utc),
                    payload_hash=digest,
                    storage_path=rel,
                    content_type=ctype,
                )
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_err = exc
                if attempt < self.settings.max_retries:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"fetch failed after {self.settings.max_retries} attempts: {url} ({last_err})")


def load_archived(settings: Settings, storage_path: str) -> bytes:
    with gzip.open(settings.raw_dir / storage_path, "rb") as fh:
        return fh.read()
