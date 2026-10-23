"""Harvest publisher archives (sitemap-driven). Run after the news channel, before scoring.

    .venv/bin/python scripts/harvest_archive.py --sites ttm nt automotive wattisduurzaam change schuttevaer --max-articles 250
"""
from __future__ import annotations

import argparse
import time

from depotmon.config import Settings
from depotmon.ingest import archive
from depotmon.store import Store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", nargs="+", default=list(archive.SITES))
    ap.add_argument("--max-articles", type=int, default=250)
    ap.add_argument("--max-sitemaps", type=int, default=None)
    ap.add_argument("--offset", type=int, default=0,
                    help="skip this many unfetched candidates (to walk deeper history)")
    ap.add_argument("--sleep", type=float, default=2.0)
    args = ap.parse_args()

    settings = Settings()
    store = Store(settings.database_url)
    grand: dict[str, int] = {}
    for site in args.sites:
        try:
            stats = archive.run(store, settings, site=site,
                                max_articles=args.max_articles,
                                max_sitemaps=args.max_sitemaps,
                                offset=args.offset)
        except Exception as exc:  # noqa: BLE001 — one publisher must not stop the rest
            print(f"{site}: FAILED {exc}", flush=True)
            continue
        print(f"{site:16s} urls={stats['urls']:6d} cand={stats['candidates']:5d} "
              f"fetched={stats['fetched']:4d} kept={stats['kept']:4d} "
              f"new={stats['new']:3d} known={stats.get('known_skipped', 0):5d} "
              f"err={stats['errors']:3d}", flush=True)
        for key in ("fetched", "kept", "new", "updated", "unchanged"):
            grand[key] = grand.get(key, 0) + stats[key]
        time.sleep(args.sleep)
    print("TOTAL", grand, flush=True)


if __name__ == "__main__":
    main()
