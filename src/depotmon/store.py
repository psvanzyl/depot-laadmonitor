"""PostgreSQL/PostGIS store: migrations, runs, signals, sites, evidence, alerts."""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


UPDATE_SIGNAL_FIELDS = """
    UPDATE signals SET last_seen=now(), content_hash=%(content_hash)s,
           raw_state=%(raw_state)s, status_hint=%(status_hint)s, power_kw=%(power_kw)s,
           n_points=%(n_points)s, connector_types=%(connector_types)s,
           hgv_capable=%(hgv_capable)s, access=%(access)s, text_excerpt=%(text_excerpt)s,
           operator=COALESCE(%(operator)s, operator), city=COALESCE(%(city)s, city),
           postcode=COALESCE(%(postcode)s, postcode), address=COALESCE(%(address)s, address)
     WHERE signal_id=%(signal_id)s
"""

class Store:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.conn = psycopg.connect(database_url, autocommit=False, row_factory=dict_row)

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[psycopg.Cursor]:
        with self.conn.cursor() as cur:
            try:
                yield cur
            except Exception:
                # Without this the connection stays in "aborted" state and every later
                # statement fails with a misleading error.
                self.conn.rollback()
                raise
        self.conn.commit()

    def migrate(self) -> list[str]:
        applied: list[str] = []
        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        with self.conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            self.conn.commit()
            cur.execute("SELECT version FROM schema_migrations")
            done = {r["version"] for r in cur.fetchall()}
            for f in files:
                if f.name in done:
                    continue
                cur.execute(f.read_text(encoding="utf-8"))
                cur.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (f.name,))
                self.conn.commit()
                applied.append(f.name)
        return applied

    # -- runs --------------------------------------------------------------
    def start_run(self, kind: str, source: str | None = None) -> int:
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO runs(kind, source) VALUES (%s, %s) RETURNING run_id",
                (kind, source),
            )
            return cur.fetchone()["run_id"]

    def finish_run(
        self,
        run_id: int,
        *,
        status: str = "ok",
        records_in: int = 0,
        records_out: int = 0,
        note: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.tx() as cur:
            cur.execute(
                "UPDATE runs SET finished_at=now(), status=%s, records_in=%s, records_out=%s,"
                " note=%s, error=%s WHERE run_id=%s",
                (status, records_in, records_out, note, error, run_id),
            )

    # -- raw archive index -------------------------------------------------
    def record_raw(
        self,
        *,
        payload_hash: str,
        source: str,
        url: str,
        run_id: int | None,
        content_type: str | None,
        http_status: int | None,
        n_bytes: int,
        storage_path: str,
    ) -> None:
        with self.tx() as cur:
            cur.execute(
                """INSERT INTO raw_payloads(payload_hash, source, url, run_id, content_type,
                                            http_status, bytes, storage_path)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (payload_hash) DO UPDATE SET fetched_at=now()""",
                (payload_hash, source, url, run_id, content_type, http_status, n_bytes, storage_path),
            )

    # -- alerts ------------------------------------------------------------
    def alert(self, kind: str, message: str, source: str | None = None,
              payload: dict | None = None) -> None:
        log.warning("ALERT [%s] %s", kind, message)
        with self.tx() as cur:
            cur.execute(
                "INSERT INTO alerts(kind, message, source, payload) VALUES (%s,%s,%s,%s)",
                (kind, message, source, Jsonb(payload or {})),
            )

    def record_health(self, source: str, ok: bool, records: int,
                      error: str | None = None, deviation: float | None = None) -> None:
        with self.tx() as cur:
            cur.execute(
                """INSERT INTO source_health(source, day, ok, records, deviation, error)
                   VALUES (%s, CURRENT_DATE, %s, %s, %s, %s)
                   ON CONFLICT (source, day) DO UPDATE
                     SET ok=EXCLUDED.ok, records=EXCLUDED.records,
                         deviation=EXCLUDED.deviation, error=EXCLUDED.error, checked_at=now()""",
                (source, ok, records, deviation, error),
            )

    def baseline_count(self, source: str, days: int = 14) -> float | None:
        """Median daily record count over the last `days` completed days."""
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY records) AS med
                   FROM source_health
                   WHERE source=%s AND ok AND day >= CURRENT_DATE - %s
                     AND day < CURRENT_DATE""",
                (source, days),
            )
            row = cur.fetchone()
            return float(row["med"]) if row and row["med"] is not None else None

    # -- signals -----------------------------------------------------------
    def upsert_signal(self, sig: dict[str, Any]) -> str:
        """Insert-or-refresh one signal. Returns 'new' | 'updated' | 'unchanged'."""
        with self.tx() as cur:
            cur.execute(
                "SELECT content_hash FROM signals WHERE signal_id=%s", (sig["signal_id"],)
            )
            prev = cur.fetchone()
            if prev is None:
                cur.execute(
                    """INSERT INTO signals(signal_id, source, source_key, kind, name, operator,
                        address, postcode, city, lon, lat, bag_id, power_kw, n_points,
                        connector_types, hgv_capable, access, status_hint, text_excerpt,
                        doc_url, observed_at, raw_state, content_hash,
                        geom)
                       VALUES (%(signal_id)s,%(source)s,%(source_key)s,%(kind)s,%(name)s,
                        %(operator)s,%(address)s,%(postcode)s,%(city)s,%(lon)s,%(lat)s,%(bag_id)s,
                        %(power_kw)s,%(n_points)s,%(connector_types)s,%(hgv_capable)s,%(access)s,
                        %(status_hint)s,%(text_excerpt)s,%(doc_url)s,%(observed_at)s,
                        %(raw_state)s,%(content_hash)s,
                        CASE WHEN %(lon)s::double precision IS NULL
                                   OR %(lat)s::double precision IS NULL THEN NULL
                             ELSE ST_Transform(ST_SetSRID(ST_MakePoint(
                                  %(lon)s::double precision, %(lat)s::double precision),4326),28992)
                        END)""",
                    {**sig, "raw_state": Jsonb(sig["raw_state"])},
                )
                return "new"
            if prev["content_hash"] == sig["content_hash"]:
                # Same text, but extraction rules may have improved: refresh the fields
                # too, otherwise a fixed parser never reaches existing rows.
                cur.execute(
                    UPDATE_SIGNAL_FIELDS, {**sig, "raw_state": Jsonb(sig["raw_state"])}
                )
                return "unchanged"
            cur.execute(
                UPDATE_SIGNAL_FIELDS, {**sig, "raw_state": Jsonb(sig["raw_state"])}
            )
            return "updated"

    def signals_since(self, kind: str | None = None, days: int = 400) -> list[dict]:
        with self.conn.cursor() as cur:
            q = "SELECT * FROM signals WHERE last_seen >= now() - make_interval(days => %s)"
            args: list[Any] = [days]
            if kind:
                q += " AND kind=%s"
                args.append(kind)
            cur.execute(q, args)
            return list(cur.fetchall())

    # -- fleet stats -------------------------------------------------------
    def upsert_fleet(self, metric: str, period: str, region: str, value: float,
                     source: str) -> None:
        with self.tx() as cur:
            cur.execute(
                """INSERT INTO fleet_stats(metric, period, region, value, source)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (metric, period, region) DO UPDATE
                     SET value=EXCLUDED.value, fetched_at=now()""",
                (metric, period, region, value, source),
            )

    def fleet_series(self, metric: str, region: str = "NL") -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT period, value FROM fleet_stats WHERE metric=%s AND region=%s ORDER BY period",
                (metric, region),
            )
            return list(cur.fetchall())


def today() -> date:
    return datetime.now(timezone.utc).date()


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
