"""Command line entry point: python -m depotmon ... / depotmon ..."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .classify import status as classify
from .config import get_settings
from .diff import runner as diff_runner
from .resolve import geocode, sites as resolve_sites
from .store import Store

log = logging.getLogger("depotmon")

INGESTS = {
    "permits": ("depotmon.ingest.permits", "run"),
    "news": ("depotmon.ingest.news", "run"),
    "ndw": ("depotmon.ingest.ndw", "run"),
    "osm": ("depotmon.ingest.osm", "run"),
    "osm_companies": ("depotmon.ingest.osm_companies", "run"),
    "rdw": ("depotmon.ingest.rdw", "run"),
    "cbs": ("depotmon.ingest.cbs", "run"),
    "ocm": ("depotmon.ingest.ocm", "run"),
    "depotcharge": ("depotmon.ingest.depotcharge", "run"),
    "etrucker": ("depotmon.ingest.etrucker", "run"),
}


def _ingest(store: Store, settings, name: str, **kwargs) -> dict:
    import importlib

    module_path, func = INGESTS[name]
    mod = importlib.import_module(module_path)
    fn = getattr(mod, func)
    try:
        return fn(store, settings, **kwargs)
    except TypeError:
        return fn(store, settings)


def cmd_init(store: Store, settings, args) -> None:
    settings.ensure_dirs()
    applied = store.migrate()
    print(json.dumps({"migrations_applied": applied, "database": "ok"}, indent=2))


def cmd_ingest(store: Store, settings, args) -> None:
    targets = list(INGESTS) if args.source == "all" else [args.source]
    results = {}
    for name in targets:
        try:
            kwargs = {}
            if name == "permits":
                kwargs["days"] = args.days
                if getattr(args, "max_records", None):
                    kwargs["max_records"] = args.max_records
            if name == "rdw":
                kwargs["months"] = args.months or settings.rdw_months
                kwargs["refresh"] = args.refresh
            results[name] = _ingest(store, settings, name, **kwargs)
            print(f"[ok] {name}: {json.dumps(results[name], default=str)[:400]}")
        except Exception as exc:
            results[name] = {"error": str(exc)}
            print(f"[fail] {name}: {exc}", file=sys.stderr)
    print(json.dumps(results, indent=2, default=str))


def cmd_resolve(store: Store, settings, args) -> None:
    print(json.dumps(geocode.enrich_signals(store, settings, limit=args.limit), indent=2))
    print(json.dumps(resolve_sites.build_sites(store, settings), indent=2))


def cmd_classify(store: Store, settings, args) -> None:
    print(json.dumps(classify.run(store, settings), indent=2))


def cmd_score(store: Store, settings, args) -> None:
    """Stack evidence per business premises -> tier A/B/C (private sites only)."""
    from .classify import premises
    from .resolve.postcodes import fill_missing_places

    try:
        # A bare postcode is a coordinate, not a lead: name it before ranking.
        filled = fill_missing_places(store, settings)
    except Exception as exc:  # enrichment must never block scoring
        log.warning("postcode enrichment failed: %s", exc)
        filled = 0
    result = premises.run(store, settings)
    result["counts"]["places_filled"] = filled
    print(json.dumps(result["counts"], indent=2))
    for row in result["premises"][: args.limit]:
        print(f"[{row['tier']}] {row['score']:.2f}  {row['operator']} "
              f"({row['city'] or '?'})  {row['power_kw'] or '?'} kW  "
              f"{len(row['evidence'])} evidence")


def cmd_diff(store: Store, settings, args) -> None:
    print(json.dumps(diff_runner.run(store, settings), indent=2, default=str))


def cmd_report(store: Store, settings, args) -> None:
    from .report import weekly

    print(json.dumps(weekly.build(store, settings, week=args.week), indent=2, default=str))


def cmd_daily(store: Store, settings, args) -> None:
    """Full scheduled cycle: ingest everything, resolve, classify, diff."""
    summary = {}
    for name in ["permits", "news", "ndw", "osm", "cbs", "depotcharge", "etrucker"]:
        try:
            summary[name] = _ingest(store, settings, name)
        except Exception as exc:
            summary[name] = {"error": str(exc)}
            print(f"[fail] {name}: {exc}", file=sys.stderr)
    try:
        summary["geocode"] = geocode.enrich_signals(store, settings)
        summary["sites"] = resolve_sites.build_sites(store, settings)
        summary["classify"] = classify.run(store, settings)
        try:
            from .classify import premises

            summary["score"] = premises.run(store, settings)["counts"]
        except Exception as exc:  # scoring must never block the daily cycle
            summary["score_error"] = str(exc)
        summary["diff"] = diff_runner.run(store, settings)
    except Exception as exc:
        summary["pipeline_error"] = str(exc)
        print(f"[fail] pipeline: {exc}", file=sys.stderr)
        raise
    print(json.dumps(summary, indent=2, default=str))
    if any(isinstance(v, dict) and v.get("error") for v in summary.values()):
        sys.exit(2)


def cmd_weekly(store: Store, settings, args) -> None:
    from .report import weekly

    print(json.dumps(weekly.build(store, settings), indent=2, default=str))


def cmd_serve(store: Store, settings, args) -> None:
    import uvicorn

    uvicorn.run("depotmon.api.app:app", host=args.host, port=args.port, log_level="info")


def cmd_stats(store: Store, settings, args) -> None:
    with store.conn.cursor() as cur:
        cur.execute("SELECT status, COUNT(*) AS n FROM sites GROUP BY status ORDER BY n DESC")
        by_status = list(cur.fetchall())
        cur.execute("SELECT COUNT(*) AS n FROM sites")
        total = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM signals")
        signals = cur.fetchone()["n"]
        cur.execute(
            """SELECT source, day, ok, records, error FROM source_health
               ORDER BY source, day DESC LIMIT 12"""
        )
        health = list(cur.fetchall())
        cur.execute("SELECT COUNT(*) AS n FROM alerts WHERE NOT acknowledged")
        alerts = cur.fetchone()["n"]
    print(json.dumps({"sites": total, "signals": signals, "by_status": by_status,
                      "health": health, "open_alerts": alerts}, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="depotmon", description="Depot charging monitor NL")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="apply migrations")
    sub.add_parser("stats", help="database summary")

    pi = sub.add_parser("ingest", help="run one or all ingesters")
    pi.add_argument("source", choices=[*INGESTS, "all"])
    pi.add_argument("--days", type=int, default=None)
    pi.add_argument("--max-records", dest="max_records", type=int, default=None)
    pi.add_argument("--months", type=int, default=None)
    pi.add_argument("--refresh", action="store_true")

    pr = sub.add_parser("resolve", help="geocode + cluster into sites")
    pr.add_argument("--limit", type=int, default=400)

    sub.add_parser("classify", help="assign status + confidence")
    psc = sub.add_parser("score", help="rank private premises by stacked evidence")
    psc.add_argument("--limit", type=int, default=25)

    sub.add_parser("diff", help="diff against previous run")
    prp = sub.add_parser("report", help="build weekly report")
    prp.add_argument("--week", default=None)
    sub.add_parser("daily", help="full daily cycle")
    sub.add_parser("weekly", help="weekly report")

    ps = sub.add_parser("serve", help="run the API")
    ps.add_argument("--host", default="0.0.0.0")
    ps.add_argument("--port", type=int, default=8080)

    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    handler = {
        "init": cmd_init,
        "stats": cmd_stats,
        "ingest": cmd_ingest,
        "resolve": cmd_resolve,
        "classify": cmd_classify,
        "score": cmd_score,
        "diff": cmd_diff,
        "report": cmd_report,
        "daily": cmd_daily,
        "weekly": cmd_weekly,
        "serve": cmd_serve,
    }[args.command]

    store = Store(settings.database_url)
    try:
        if args.command == "init":
            handler(store, settings, args)
        elif args.command == "serve" or args.command == "stats":
            handler(store, settings, args)
        else:
            handler(store, settings, args)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
