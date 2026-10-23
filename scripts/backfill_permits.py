"""Backfill permit notices month by month (SRU cannot page past 10,000 records).

Run on CT141:  .venv/bin/python scripts/backfill_permits.py --months 24
"""
from __future__ import annotations

import argparse
import time
from datetime import date, timedelta

from depotmon.config import Settings
from depotmon.ingest import permits
from depotmon.store import Store


def month_windows(months: int) -> list[tuple[date, date]]:
    end = date.today().replace(day=1)
    windows: list[tuple[date, date]] = []
    for _ in range(months):
        start = (end - timedelta(days=1)).replace(day=1)
        windows.append((start, end))
        end = start
    return windows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--sleep", type=float, default=3.0, help="pause between windows")
    ap.add_argument("--stop-at", default=None, help="stop before this YYYY-MM (resume point)")
    ap.add_argument("--from", dest="start_at", default=None, help="skip windows newer than YYYY-MM")
    args = ap.parse_args()

    settings = Settings()
    store = Store(settings.database_url)
    totals: dict[str, int] = {}
    for start, end in month_windows(args.months):
        if args.start_at and start.isoformat()[:7] > args.start_at:
            continue
        if args.stop_at and start.isoformat()[:7] <= args.stop_at:
            print(f"reached --stop-at {args.stop_at}; stopping", flush=True)
            break
        stats = permits.run(store, settings, since=start, until=end, max_records=8000)
        for key, val in stats.items():
            if isinstance(val, int):
                totals[key] = totals.get(key, 0) + val
        print(f"{start} .. {end}  records={stats['records']:5d} "
              f"fetched={stats['docs_fetched']:4d} relevant={stats['relevant']:3d} "
              f"new={stats['new']:3d}", flush=True)
        time.sleep(args.sleep)
    print("TOTAL", totals, flush=True)


if __name__ == "__main__":
    main()
