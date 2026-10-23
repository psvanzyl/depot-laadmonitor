"use client";

import { useEffect, useMemo, useRef } from "react";
import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { TIER_COLOR, type Depots, type Tier } from "@/lib/types";

// The company base layer: every known transport/logistics premises in NL. Depot
// findings are drawn on top of it, at the company's own yard where we can prove it.
type Companies = { type: "FeatureCollection"; features: unknown[] } | null;

const NL_BOUNDS: [[number, number], [number, number]] = [
  [3.2, 50.7],
  [7.3, 53.6],
];

// Key-free raster basemap. CARTO started demanding an API key and served
// "API KEY REQUIRED" placeholder tiles; OSM's own tiles need no account.
const BASEMAP_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    basemap: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution:
        '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>-bijdragers',
    },
  },
  layers: [{ id: "basemap", type: "raster", source: "basemap" }],
};

interface Props {
  depots: Depots | null;
  companies?: Companies;
  showCompanies?: boolean;
  minKw?: number;
  tiers: Tier[];
  hgvOnly: boolean;
  focus: { id: string; lon: number; lat: number } | null;
  onSelect: (id: string) => void;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!),
  );
}

function lngLatOf(f: maplibregl.MapGeoJSONFeature): [number, number] {
  return (f.geometry as unknown as { coordinates: [number, number] }).coordinates;
}

function popupHtml(p: Record<string, unknown>): string {
  const tier = String(p.tier ?? "C") as Tier;
  const evidence = Array.isArray(p.evidence) ? (p.evidence as string[]) : [];
  const links = evidence
    .slice(0, 3)
    .map(
      (u, i) =>
        `<a href="${escapeHtml(u)}" target="_blank" rel="noopener" style="color:#b45309">bron ${i + 1}</a>`,
    )
    .join(" · ");
  const kw = p.powerKw ? `${Number(p.powerKw)} kW` : "vermogen onbekend";
  const points = p.nPoints ? ` · ${Number(p.nPoints)} laadpunten` : "";
  // A city centroid is not a yard. Say so instead of implying precision.
  const approx =
    p.locationBasis === "city"
      ? `<div style="color:#b91c1c">locatie bij benadering (plaatsniveau) — bedrijfspand niet bevestigd</div>`
      : "";
  return `<div style="padding:9px 11px;font:12px/1.45 system-ui;max-width:260px">
      <div style="font-weight:700;color:${TIER_COLOR[tier]}">${escapeHtml(String(p.operator ?? "Onbekend"))}</div>
      <div>${escapeHtml(String(p.address || p.city || ""))}${
        p.address && p.city ? ` · ${escapeHtml(String(p.city))}` : ""
      }</div>
      <div style="color:#475569">tier ${tier} · score ${Number(p.score).toFixed(2)} · ${kw}${points}</div>
      <div style="color:#475569">${p.hgv ? "HGV-geschikt · " : ""}${Number(p.evidenceCount) || 0} bewijsstuk(ken)</div>
      ${approx}
      ${links ? `<div style="margin-top:4px">${links}</div>` : ""}
    </div>`;
}

export default function MapView({
  depots,
  companies = null,
  showCompanies = true,
  minKw = 0,
  tiers,
  hgvOnly,
  focus,
  onSelect,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const tiersRef = useRef<Tier[]>(tiers);
  tiersRef.current = tiers;

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASEMAP_STYLE,
      bounds: NL_BOUNDS,
      fitBoundsOptions: { padding: 28 },
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Company base layer — context for the findings, added first so depots sit on top.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !companies) return;
    const ensure = () => {
      if (map.getSource("companies")) return;
      map.addSource("companies", { type: "geojson", data: companies as never });
      map.addLayer({
        id: "companies",
        type: "circle",
        source: "companies",
        paint: {
          "circle-radius": 2.6,
          "circle-color": ["match", ["get", "kind"], "carrier", "#0f766e", "#64748b"],
          "circle-opacity": 0.45,
        },
      });
      map.on("click", "companies", (e) => {
        const f = e.features?.[0];
        if (!f) return;
        const p = f.properties as Record<string, unknown>;
        popupRef.current?.remove();
        popupRef.current = new maplibregl.Popup({ closeButton: true, maxWidth: "260px" })
          .setLngLat(lngLatOf(f))
          .setHTML(
            `<div style="padding:8px 10px;font:12px/1.4 system-ui;max-width:240px">
               <div style="font-weight:600">${escapeHtml(String(p.name ?? ""))}</div>
               <div style="color:#475569">${escapeHtml(String(p.address || p.city || ""))}</div>
               <div style="color:#94a3b8;font-size:11px">transportbedrijf · geen laadbewijs</div>
             </div>`,
          )
          .addTo(map);
      });
      map.on("mouseenter", "companies", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "companies", () => (map.getCanvas().style.cursor = ""));
    };
    if (map.isStyleLoaded()) ensure();
    else map.once("load", ensure);
  }, [companies]);

  // Source + layer, added once the style is ready.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !depots) return;
    const ensure = () => {
      if (!map.getSource("depots")) {
        map.addSource("depots", { type: "geojson", data: depots as never });
        // Radius grows with power where known; unknown power keeps a small dot
        // instead of pretending to a number nobody published.
        map.addLayer({
          id: "depots",
          type: "circle",
          source: "depots",
          paint: {
            "circle-radius": [
              "interpolate",
              ["linear"],
              ["coalesce", ["get", "powerKw"], 0],
              0,
              5,
              400,
              7,
              1200,
              10,
              3000,
              14,
            ],
            "circle-color": [
              "match",
              ["get", "tier"],
              "A",
              TIER_COLOR.A,
              "B",
              TIER_COLOR.B,
              TIER_COLOR.C,
            ],
            "circle-opacity": ["match", ["get", "tier"], "C", 0.65, 0.9],
            "circle-stroke-color": "#ffffff",
            "circle-stroke-width": 1.2,
          },
        });
        map.on("click", "depots", (e) => {
          const f = e.features?.[0];
          if (!f) return;
          const p = f.properties as Record<string, unknown>;
          popupRef.current?.remove();
          popupRef.current = new maplibregl.Popup({ closeButton: true, maxWidth: "280px" })
            .setLngLat(lngLatOf(f))
            .setHTML(popupHtml(p))
            .addTo(map);
          if (p.id) onSelect(String(p.id));
        });
        map.on("mouseenter", "depots", () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", "depots", () => (map.getCanvas().style.cursor = ""));
      }
    };
    if (map.isStyleLoaded()) ensure();
    else map.once("load", ensure);
  }, [depots]); // eslint-disable-line react-hooks/exhaustive-deps

  // Tier + HGV filters are client-side, so the chips work without a rebuild.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer("depots")) return;
    const filters: unknown[] = [["in", ["get", "tier"], ["literal", tiers]]];
    if (hgvOnly) filters.push(["==", ["get", "hgv"], true]);
    if (minKw > 0) filters.push([">=", ["coalesce", ["get", "powerKw"], 0], minKw]);
    map.setFilter("depots", ["all", ...filters] as never);
  }, [tiers, hgvOnly, minKw, depots]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer("companies")) return;
    map.setLayoutProperty("companies", "visibility", showCompanies ? "visible" : "none");
  }, [showCompanies, companies]);

  // Sidebar click -> fly to the site and open its popup.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focus || !depots) return;
    const feature = depots.features.find((f) => f.properties.id === focus.id);
    if (!feature) return;
    map.flyTo({ center: [focus.lon, focus.lat], zoom: 12, speed: 0.9 });
    popupRef.current?.remove();
    popupRef.current = new maplibregl.Popup({ closeButton: true, maxWidth: "280px" })
      .setLngLat([focus.lon, focus.lat])
      .setHTML(popupHtml(feature.properties as unknown as Record<string, unknown>))
      .addTo(map);
  }, [focus, depots]);

  // MapLibre sets position:relative on its own container, which beats Tailwind's
  // utilities layer and silently collapses the map to zero height — so the
  // wrapper carries inline styles and the inner div only gets h-full/w-full.
  return (
    <div style={{ position: "absolute", inset: 0 }}>
      <div ref={containerRef} className="h-full w-full" />
    </div>
  );
}
