"""Configuration — everything overridable by environment variable."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "DATABASE_URL",
            "postgresql://depotmon:depotmon@localhost:5432/depotmon",
        )
    )
    raw_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("RAW_DIR", "/data/raw"))
    )
    report_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("REPORT_DIR", "/data/reports"))
    )
    # Depotcharge has no API and must not be polled, so the ingest reads a
    # snapshot captured from an authenticated browser session.
    etrucker_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("ETRUCKER_DIR", "/data/etrucker"))
    )
    etrucker_source_url: str = field(
        default_factory=lambda: os.environ.get(
            "ETRUCKER_SOURCE_URL", "https://etrucker.io"
        )
    )
    depotcharge_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("DEPOTCHARGE_DIR", "/data/depotcharge"))
    )
    depotcharge_fleet_url: str = field(
        default_factory=lambda: os.environ.get(
            "DEPOTCHARGE_FLEET_URL",
            "https://hub.depotcharge.com/fleet/map"
            "?fleetId=52c2bebe-2419-4954-a079-8a625844abea",
        )
    )
    user_agent: str = field(
        default_factory=lambda: os.environ.get(
            "USER_AGENT",
            "depot-laadmonitor/0.1 (+https://github.com/psvanzyl/depot-laadmonitor; "
            "open-data research; contact psvanzyl@gmail.com)",
        )
    )
    # politeness
    min_interval: float = field(default_factory=lambda: _float("MIN_INTERVAL", 1.0))
    overpass_interval: float = field(
        default_factory=lambda: _float("OVERPASS_INTERVAL", 5.0)
    )
    http_timeout: float = field(default_factory=lambda: _float("HTTP_TIMEOUT", 90.0))
    max_retries: int = field(default_factory=lambda: _int("MAX_RETRIES", 3))
    respect_robots: bool = field(default_factory=lambda: _bool("RESPECT_ROBOTS", True))

    # KOOP SRU 2.0 (Officiële Bekendmakingen): a documented reuse API, not a crawl
    # target. repository.overheid.nl serves `Disallow: /` to crawlers while KOOP
    # publishes a manual for exactly this programmatic use, so the host is exempted
    # from OUR robots check under a strict request budget. Set SRU_ENABLED=false to
    # switch the channel off entirely.
    sru_enabled: bool = field(default_factory=lambda: _bool("SRU_ENABLED", True))
    sru_max_keywords: int = field(default_factory=lambda: _int("SRU_MAX_KEYWORDS", 8))
    sru_contact: str = field(
        default_factory=lambda: os.getenv("SRU_CONTACT", "ops@laserraptorai.duckdns.org")
    )

    # ingest tuning
    permit_lookback_days: int = field(
        default_factory=lambda: _int("PERMIT_LOOKBACK_DAYS", 10)
    )
    # 400 truncated a 24-month backfill (10,210 records) without telling anyone.
    permit_max_records: int = field(
        default_factory=lambda: _int("PERMIT_MAX_RECORDS", 20000)
    )
    permit_fetch_docs: bool = field(
        default_factory=lambda: _bool("PERMIT_FETCH_DOCS", True)
    )
    min_power_kw: float = field(default_factory=lambda: _float("MIN_POWER_KW", 100.0))
    rdw_months: int = field(default_factory=lambda: _int("RDW_MONTHS", 18))

    # private-site discovery channel: publisher-owned trade-press RSS feeds
    news_feeds: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            f.strip()
            for f in os.environ.get(
                "NEWS_FEEDS",
                "https://www.ttm.nl/feed/,"
                "https://www.nieuwsbladtransport.nl/feed/,"
                "https://www.automotive-online.nl/feed/,"
                "https://www.tankpro.nl/feed/,"
                "https://www.wattisduurzaam.nl/feed/,"
                "https://www.change.inc/rss,"
                "https://www.schuttevaer.nl/rss,"
                "https://www.binnenvaartkrant.nl/feed/",
            ).split(",")
            if f.strip()
        )
    )

    # optional / flagged
    ocm_api_key: str = field(default_factory=lambda: os.environ.get("OCM_API_KEY", ""))
    llm_classify: bool = field(default_factory=lambda: _bool("LLM_CLASSIFY", False))
    llm_base_url: str = field(
        default_factory=lambda: os.environ.get("LLM_BASE_URL", "http://192.168.178.162:1933/v1")
    )
    llm_model: str = field(
        default_factory=lambda: os.environ.get("LLM_MODEL", "gpt-oss:120b")
    )
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))
    aerial_verify: bool = field(default_factory=lambda: _bool("AERIAL_VERIFY", False))

    alert_webhook_url: str = field(
        default_factory=lambda: os.environ.get("ALERT_WEBHOOK_URL", "")
    )
    count_deviation_factor: float = field(
        default_factory=lambda: _float("COUNT_DEVIATION_FACTOR", 3.0)
    )

    api_title: str = "Depot Laadmonitor NL"

    def ensure_dirs(self) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
