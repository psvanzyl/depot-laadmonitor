"""Diff: compare the current site set against the previous snapshot."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from ..config import Settings
from ..store import Store

ACTIVE_WINDOW_DAYS = 400


def _json_default(obj):
    """psycopg hands back Decimal for numeric and datetime for timestamptz.

    Both are normal in a change payload, neither is JSON. Convert rather than
    stringify where the type has an obvious JSON equivalent, so the stored
    payload stays machine-readable for the webapp.
    """
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


def run(store: Store, settings: Settings) -> dict:
    run_id = store.start_run("diff")
    stats = {"new": 0, "changed": 0, "removed": 0, "unchanged": 0, "active": 0}
    try:
        with store.conn.cursor() as cur:
            cur.execute(
                """SELECT site_id, status, confidence, power_kw_announced,
                          power_kw_operational, n_points, state_hash
                   FROM sites
                   WHERE last_seen >= now() - make_interval(days => %s)""",
                (ACTIVE_WINDOW_DAYS,),
            )
            current = {str(r["site_id"]): r for r in cur.fetchall()}
        stats["active"] = len(current)

        with store.conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT run_id FROM site_snapshots
                   WHERE run_id <> %s ORDER BY run_id DESC LIMIT 1""",
                (run_id,),
            )
            prev_row = cur.fetchone()
        previous: dict[str, dict] = {}
        if prev_row:
            with store.conn.cursor() as cur:
                cur.execute(
                    """SELECT site_id, status, confidence, power_kw_announced,
                              power_kw_operational, n_points, state_hash
                       FROM site_snapshots WHERE run_id=%s""",
                    (prev_row["run_id"],),
                )
                previous = {str(r["site_id"]): r for r in cur.fetchall()}

        changes: list[tuple[str, str, dict]] = []
        for site_id, row in current.items():
            if site_id not in previous:
                changes.append((site_id, "new", {
                    "status": row["status"],
                    "power_kw_announced": float(row["power_kw_announced"] or 0) or None,
                    "power_kw_operational": float(row["power_kw_operational"] or 0) or None,
                }))
            elif previous[site_id]["state_hash"] != row["state_hash"]:
                fields = {}
                for key in ("status", "confidence", "power_kw_announced",
                            "power_kw_operational", "n_points"):
                    old, new = previous[site_id][key], row[key]
                    if isinstance(old, float) or isinstance(new, float):
                        old = float(old or 0)
                        new = float(new or 0)
                    if old != new:
                        fields[key] = {"old": old, "new": new}
                changes.append((site_id, "changed", fields))
            else:
                stats["unchanged"] += 1
        for site_id, row in previous.items():
            if site_id not in current:
                changes.append((site_id, "removed", {"last_status": row["status"]}))

        with store.tx() as cur:
            for site_id, row in current.items():
                cur.execute(
                    """INSERT INTO site_snapshots(run_id, site_id, status, confidence,
                        power_kw_announced, power_kw_operational, n_points, state_hash)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (run_id, site_id) DO NOTHING""",
                    (run_id, site_id, row["status"], row["confidence"],
                     row["power_kw_announced"], row["power_kw_operational"],
                     row["n_points"], row["state_hash"]),
                )
            for site_id, change_type, fields in changes:
                cur.execute(
                    """INSERT INTO changes(run_id, site_id, change_type, fields)
                       VALUES (%s,%s,%s,%s)""",
                    (run_id, site_id, change_type, json.dumps(fields, default=_json_default)),
                )
                stats[change_type] += 1

        store.finish_run(run_id, status="ok", records_in=len(current),
                         records_out=len(changes), note=json.dumps(stats))
        return stats | {"run_id": run_id, "previous_run_id": prev_row["run_id"] if prev_row else None}
    except Exception as exc:
        store.finish_run(run_id, status="failed", error=str(exc)[:2000])
        raise


def latest_changes(store: Store, limit: int = 200) -> list[dict]:
    with store.conn.cursor() as cur:
        cur.execute(
            """SELECT c.run_id, c.site_id, c.change_type, c.fields, c.created_at,
                      s.name, s.status, s.province, s.municipality
               FROM changes c LEFT JOIN sites s USING (site_id)
               ORDER BY c.change_id DESC LIMIT %s""",
            (limit,),
        )
        return list(cur.fetchall())
