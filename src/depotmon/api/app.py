"""FastAPI app: /sites, /sites/{id}, /diff, /stats, /sources + static MapLibre page."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row

from ..config import get_settings

settings = get_settings()
STATIC = Path(__file__).parent / "static"

app = FastAPI(title=settings.api_title, version="0.1.0")


def conn():
    return psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=True)


def rows(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]


SITE_FIELDS = (
    "site_id, name, operator, address, postcode, city, province, municipality, status,"
    " confidence, power_kw_announced, power_kw_operational, n_points, connector_types,"
    " hgv_capable, access, lat, lon, bag_id, first_seen, last_seen"
)


def as_feature(row: dict) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
        "properties": {k: v for k, v in row.items()
                       if k not in {"lon", "lat"} and not isinstance(v, (bytes, memoryview))},
    }


@app.get("/health")
def health():
    try:
        rows("SELECT 1 AS ok")
        return {"status": "ok"}
    except Exception as exc:  # pragma: no cover
        return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)


@app.get("/sites")
def sites(
    bbox: str | None = Query(None, description="minlon,minlat,maxlon,maxlat (EPSG:4326)"),
    status: str | None = None,
    province: str | None = None,
    municipality: str | None = None,
    min_power_kw: float | None = None,
    hgv: bool | None = None,
    active_days: int = 400,
    limit: int = 1000,
    offset: int = 0,
    format: str = "json",
):
    where = ["last_seen >= now() - make_interval(days => %s)"]
    args: list[Any] = [active_days]
    if bbox:
        try:
            minlon, minlat, maxlon, maxlat = (float(x) for x in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox must be minlon,minlat,maxlon,maxlat")
        where.append("lon BETWEEN %s AND %s AND lat BETWEEN %s AND %s")
        args += [minlon, maxlon, minlat, maxlat]
    if status:
        where.append("status = %s")
        args.append(status)
    if province:
        where.append("province ILIKE %s")
        args.append(province)
    if municipality:
        where.append("municipality ILIKE %s")
        args.append(municipality)
    if min_power_kw is not None:
        where.append("GREATEST(COALESCE(power_kw_announced,0), COALESCE(power_kw_operational,0)) >= %s")
        args.append(min_power_kw)
    if hgv is not None:
        where.append("hgv_capable = %s")
        args.append(hgv)
    sql = (
        f"SELECT {SITE_FIELDS} FROM sites_wgs84 WHERE " + " AND ".join(where)
        + " ORDER BY GREATEST(COALESCE(power_kw_announced,0), COALESCE(power_kw_operational,0)) DESC"
        + " LIMIT %s OFFSET %s"
    )
    args += [min(limit, 5000), offset]
    data = rows(sql, tuple(args))
    if format == "geojson":
        return {"type": "FeatureCollection",
                "features": [as_feature(r) for r in data if r["lon"] is not None]}
    return {"count": len(data), "limit": limit, "offset": offset, "sites": data}


@app.get("/sites/{site_id}")
def site(site_id: str):
    data = rows(f"SELECT {SITE_FIELDS} FROM sites_wgs84 WHERE site_id = %s", (site_id,))
    if not data:
        raise HTTPException(404, "site not found")
    out = data[0]
    out["evidence"] = rows(
        """SELECT source, url, observed_at, excerpt, confidence
           FROM evidence WHERE site_id=%s ORDER BY confidence DESC""",
        (site_id,),
    )
    return out


@app.get("/diff")
def diff(since: str | None = None, limit: int = 500):
    if since:
        data = rows(
            """SELECT c.run_id, c.site_id, c.change_type, c.fields, c.created_at,
                      s.name, s.status, s.province, s.municipality
               FROM changes c LEFT JOIN sites s USING (site_id)
               WHERE c.created_at >= %s::timestamptz
               ORDER BY c.change_id DESC LIMIT %s""",
            (since, min(limit, 5000)),
        )
    else:
        data = rows(
            """SELECT c.run_id, c.site_id, c.change_type, c.fields, c.created_at,
                      s.name, s.status, s.province, s.municipality
               FROM changes c LEFT JOIN sites s USING (site_id)
               ORDER BY c.change_id DESC LIMIT %s""",
            (min(limit, 5000),),
        )
    counts: dict[str, int] = {}
    for r in data:
        counts[r["change_type"]] = counts.get(r["change_type"], 0) + 1
    return {"since": since, "counts": counts, "changes": data}


@app.get("/stats")
def stats():
    summary = rows(
        """SELECT status, COUNT(*) AS sites,
                  COALESCE(SUM(power_kw_announced),0) AS announced_kw,
                  COALESCE(SUM(power_kw_operational),0) AS operational_kw
           FROM sites_wgs84 WHERE last_seen >= now() - interval '400 days'
           GROUP BY status ORDER BY sites DESC"""
    )
    provinces = rows(
        """SELECT COALESCE(province,'onbekend') AS province, COUNT(*) AS sites,
                  COALESCE(SUM(power_kw_announced),0) AS announced_kw,
                  COALESCE(SUM(power_kw_operational),0) AS operational_kw
           FROM sites_wgs84 WHERE last_seen >= now() - interval '400 days'
           GROUP BY 1 ORDER BY sites DESC"""
    )
    fleet = rows(
        """SELECT metric, period, region, value FROM fleet_stats
           WHERE region='NL' ORDER BY metric, period"""
    )
    return {"by_status": summary, "by_province": provinces, "fleet": fleet,
            "generated_at": date.today().isoformat()}


@app.get("/sources")
def sources():
    health = rows(
        """SELECT DISTINCT ON (source) source, day, ok, records, deviation, error, checked_at
           FROM source_health ORDER BY source, day DESC"""
    )
    alerts = rows("SELECT kind, message, created_at FROM alerts ORDER BY alert_id DESC LIMIT 20")
    return {"sources": health, "alerts": alerts}


@app.get("/reports")
def reports():
    base = Path(settings.report_dir)
    out = []
    if base.exists():
        for d in sorted(base.iterdir(), reverse=True):
            if d.is_dir():
                out.append({"week": d.name,
                            "markdown": str(d / "rapport.md"),
                            "csv": str(d / "sites.csv"),
                            "geojson": str(d / "sites.geojson")})
    return {"reports": out}


@app.get("/reports/latest/sites.geojson")
def latest_geojson():
    base = Path(settings.report_dir)
    dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True) if base.exists() else []
    for d in dirs:
        f = d / "sites.geojson"
        if f.exists():
            return FileResponse(f, media_type="application/geo+json")
    raise HTTPException(404, "no report yet")


@app.get("/reports/latest/rapport.md", response_class=PlainTextResponse)
def latest_report():
    base = Path(settings.report_dir)
    dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True) if base.exists() else []
    for d in dirs:
        f = d / "rapport.md"
        if f.exists():
            return PlainTextResponse(f.read_text(encoding="utf-8"), media_type="text/markdown")
    raise HTTPException(404, "no report yet")


if STATIC.exists():
    app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
