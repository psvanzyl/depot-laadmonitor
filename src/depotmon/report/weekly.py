"""Report: weekly markdown + CSV + GeoJSON with provincial/municipal aggregates."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path

from ..config import Settings
from ..store import Store

STATUS_ORDER = [
    "operational",
    "under_construction",
    "grid_connection_signalled",
    "permit_granted",
    "permit_requested",
    "rumoured",
    "unknown",
]

SITE_COLUMNS = [
    "site_id", "name", "operator", "address", "postcode", "city", "province",
    "municipality", "status", "confidence", "power_kw_announced", "power_kw_operational",
    "n_points", "connector_types", "hgv_capable", "access", "lat", "lon", "bag_id",
    "first_seen", "last_seen",
]


def _mw(kw) -> float:
    return round((float(kw or 0)) / 1000.0, 1)


def _fmt(value, digits: int = 0) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:,.{digits}f}".replace(",", ".")
    return str(value)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def build(store: Store, settings: Settings, *, week: str | None = None) -> dict:
    run_id = store.start_run("report")
    today = date.today()
    iso = today.isocalendar()
    week = week or f"{iso.year}-W{iso.week:02d}"
    out_dir = Path(settings.report_dir) / week
    out_dir.mkdir(parents=True, exist_ok=True)

    with store.conn.cursor() as cur:
        cur.execute("SELECT * FROM sites_wgs84 WHERE last_seen >= now() - interval '400 days'")
        sites = list(cur.fetchall())
        cur.execute(
            """SELECT source, day, ok, records, deviation, error FROM source_health
               WHERE day >= CURRENT_DATE - 14 ORDER BY source, day DESC"""
        )
        health = list(cur.fetchall())
        cur.execute(
            "SELECT kind, message, created_at FROM alerts ORDER BY alert_id DESC LIMIT 10"
        )
        alerts = list(cur.fetchall())
        cur.execute(
            """SELECT c.change_type, c.fields, s.name, s.status, s.province
               FROM changes c JOIN sites s USING (site_id)
               WHERE c.created_at >= now() - interval '7 days'
               ORDER BY c.change_id DESC LIMIT 200"""
        )
        changes = list(cur.fetchall())

    # --- aggregates ------------------------------------------------------
    by_status: dict[str, int] = {}
    for s in sites:
        by_status[s["status"]] = by_status.get(s["status"], 0) + 1
    announced_mw = _mw(sum(float(s["power_kw_announced"] or 0) for s in sites))
    operational_mw = _mw(sum(float(s["power_kw_operational"] or 0) for s in sites))
    hgv_sites = sum(1 for s in sites if s["hgv_capable"])

    provinces: dict[str, dict] = {}
    for s in sites:
        p = s["province"] or "onbekend"
        agg = provinces.setdefault(p, {"sites": 0, "operational": 0, "announced_kw": 0.0,
                                       "operational_kw": 0.0})
        agg["sites"] += 1
        if s["status"] == "operational":
            agg["operational"] += 1
        agg["announced_kw"] += float(s["power_kw_announced"] or 0)
        agg["operational_kw"] += float(s["power_kw_operational"] or 0)

    municipalities: dict[str, int] = {}
    for s in sites:
        m = s["municipality"] or "onbekend"
        municipalities[m] = municipalities.get(m, 0) + 1

    new_sites = [c for c in changes if c["change_type"] == "new"]
    status_changes = [
        c for c in changes
        if c["change_type"] == "changed" and "status" in (c["fields"] or {})
    ]
    removed = [c for c in changes if c["change_type"] == "removed"]

    rdw = store.fleet_series("rdw_ze_n2n3_first_registrations")
    cbs = store.fleet_series("cbs_ze_bedrijfsvoertuigen_stock", "NL")

    # --- markdown --------------------------------------------------------
    lines: list[str] = []
    lines.append(f"# Depot-laadmonitor NL — weekrapport {week}")
    lines.append("")
    lines.append(
        f"Gegenereerd {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"peildatum {today.isoformat()} · {len(sites)} actieve locaties"
    )
    lines.append("")
    lines.append("## Kerncijfers")
    lines.append("")
    lines.append(
        _table(
            ["Metric", "Waarde"],
            [
                ["Actieve depotsites", _fmt(len(sites))],
                ["Waarvan operationeel", _fmt(by_status.get("operational", 0))],
                ["Aangekondigd vermogen", f"{_fmt(announced_mw, 1)} MW"],
                ["Operationeel vermogen", f"{_fmt(operational_mw, 1)} MW"],
                ["Locaties met HGV-signaal", _fmt(hgv_sites)],
                ["Nieuw deze week", _fmt(len(new_sites))],
                ["Statuswijzigingen", _fmt(len(status_changes))],
                ["Vervallen / verouderd", _fmt(len(removed))],
            ],
        )
    )
    lines.append("")
    lines.append("## Statusverdeling")
    lines.append("")
    lines.append(
        _table(
            ["Status", "Locaties"],
            [[s, _fmt(by_status[s])] for s in STATUS_ORDER if s in by_status],
        )
    )
    lines.append("")
    lines.append("## Per provincie")
    lines.append("")
    lines.append(
        _table(
            ["Provincie", "Locaties", "Operationeel", "Aangekondigd MW", "Operationeel MW"],
            [
                [p, _fmt(a["sites"]), _fmt(a["operational"]),
                 _fmt(_mw(a["announced_kw"]), 1), _fmt(_mw(a["operational_kw"]), 1)]
                for p, a in sorted(provinces.items(), key=lambda kv: -kv[1]["sites"])
            ],
        )
    )
    lines.append("")
    lines.append("## Top gemeenten")
    lines.append("")
    lines.append(
        _table(
            ["Gemeente", "Locaties"],
            [[m, _fmt(c)] for m, c in sorted(municipalities.items(), key=lambda kv: -kv[1])[:20]],
        )
    )
    lines.append("")
    if new_sites:
        lines.append("## Nieuwe locaties")
        lines.append("")
        lines.append(
            _table(
                ["Locatie", "Provincie", "Status", "Vermogen"],
                [
                    [c["name"] or "–", c["province"] or "–", c["status"] or "–",
                     _fmt(c["fields"].get("power_kw_announced") if isinstance(c["fields"], dict) else None)]
                    for c in new_sites[:30]
                ],
            )
        )
        lines.append("")
    if status_changes:
        lines.append("## Statuswijzigingen")
        lines.append("")
        rows = []
        for c in status_changes[:30]:
            st = (c["fields"] or {}).get("status") or {}
            rows.append([c["name"] or "–", c["province"] or "–",
                         f"{st.get('old', '?')} → {st.get('new', '?')}"])
        lines.append(_table(["Locatie", "Provincie", "Wijziging"], rows))
        lines.append("")
    if rdw:
        ze_by_period = {
            r["period"]: float(r["value"])
            for r in store.fleet_series("rdw_ze_n2n3_first_registrations")
        }
        rdw_rows = []
        for r in rdw[-6:]:
            total = float(r["value"] or 0)
            ze = ze_by_period.get(r["period"], 0.0)
            share = f"{(100.0 * ze / total):.1f} %" if total else "–"
            rdw_rows.append([r["period"], _fmt(total), _fmt(ze), share])
        lines.append("## Fleet-context: eerste registraties N2/N3 (RDW)")
        lines.append("")
        lines.append(
            _table(
                ["Maand", "N2/N3 eerste registraties", "Waarvan zero-emissie", "Aandeel ZE"],
                rdw_rows,
            )
        )
        lines.append("")
    if cbs:
        latest = cbs[-1]
        lines.append(
            f"CBS-voorraad elektrische bedrijfsvoertuigen (vrachtauto + trekker, "
            f"{latest['period']}): **{_fmt(float(latest['value']))}** voertuigen."
        )
        lines.append("")
    lines.append("## Bronstatus (14 dagen)")
    lines.append("")
    lines.append(
        _table(
            ["Bron", "Dag", "OK", "Records", "Fout"],
            [[h["source"], str(h["day"]), "ja" if h["ok"] else "NEE", _fmt(h["records"]),
              (h["error"] or "")[:60]] for h in health[:20]],
        )
    )
    lines.append("")
    if alerts:
        lines.append("## Alerts")
        lines.append("")
        for a in alerts:
            lines.append(f"- `{a['kind']}` {a['message'][:200]}")
        lines.append("")
    lines.append("## Beperkingen")
    lines.append("")
    lines.append(
        "- Private depots achter een hek zonder roaming-aansluiting staan niet in het "
        "CPO-register (NDW) en worden dus gemist tot er een vergunning of OSM-tag opduikt.\n"
        "- Gemeenten die niet in het Gemeenteblad publiceren zijn onzichtbaar (geschat 30–50 % "
        "van de vergunningen).\n"
        "- KvK/SBI-bedrijfsvestigingen (49.41, 52.10, 52.29) zijn niet beschikbaar zonder "
        "betaalde API-key; de site-universe is daarom vraaggestuurd (vergunningen + OSM), niet "
        "compleet.\n"
        "- Netbeheerders publiceren geen machine-leesbare capaciteitskaart; "
        "`grid_connection_signalled` komt uit vergunningstekst, niet uit DSO-data.\n"
        "- Vermogens zijn de maximale waarde per locatie; sommeren over locaties overschat het "
        "werkelijk gelijktijdig gevraagde vermogen."
    )
    lines.append("")

    md_path = out_dir / "rapport.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    # --- CSV -------------------------------------------------------------
    csv_path = out_dir / "sites.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SITE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for s in sites:
            row = {k: s.get(k) for k in SITE_COLUMNS}
            row["connector_types"] = ";".join(s.get("connector_types") or [])
            writer.writerow(row)

    # --- GeoJSON (WGS84) -------------------------------------------------
    features = []
    for s in sites:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
                "properties": {
                    "site_id": str(s["site_id"]),
                    "name": s["name"],
                    "operator": s["operator"],
                    "status": s["status"],
                    "confidence": float(s["confidence"] or 0),
                    "power_kw_announced": float(s["power_kw_announced"] or 0) or None,
                    "power_kw_operational": float(s["power_kw_operational"] or 0) or None,
                    "n_points": s["n_points"],
                    "hgv_capable": s["hgv_capable"],
                    "province": s["province"],
                    "municipality": s["municipality"],
                },
            }
        )
    geojson_path = out_dir / "sites.geojson"
    geojson_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = {
        "week": week,
        "sites": len(sites),
        "by_status": by_status,
        "announced_mw": announced_mw,
        "operational_mw": operational_mw,
        "new": len(new_sites),
        "changed": len(status_changes),
        "removed": len(removed),
        "markdown": str(md_path),
        "csv": str(csv_path),
        "geojson": str(geojson_path),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if settings.alert_webhook_url:
        _post_webhook(settings.alert_webhook_url, summary)

    store.finish_run(run_id, status="ok", records_in=len(sites), note=json.dumps(summary))
    return summary


def _post_webhook(url: str, summary: dict) -> None:
    import httpx

    text = (
        f"Depot-laadmonitor {summary['week']}: {summary['sites']} sites, "
        f"{summary['announced_mw']} MW aangekondigd / {summary['operational_mw']} MW operationeel, "
        f"{summary['new']} nieuw."
    )
    try:
        httpx.post(url, json={"text": text, "summary": summary}, timeout=20)
    except Exception:
        pass
