"use client";

import { useEffect, useMemo, useRef } from "react";
import maplibregl, { type Map as MlMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { TIER_COLOR, type AccessClass, type Depots, type Tier } from "@/lib/types";

// The company base layer: every known transport/logistics premises in NL. Depot
// findings are drawn on top of it, at the company's own yard where we can prove it.
type Companies = { type: "FeatureCollection"; features: unknown[] } | null;

// The data is Europe-wide now: NL, DE, FR, IT, ES, PL, SE, NO ... The first
// view has to contain the continent or most of the map is empty on load.
const EUROPE_BOUNDS: [[number, number], [number, number]] = [
  [-11.0, 33.0],
  [31.0, 71.5],
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
  coverage?: { type: string; features: unknown[] } | null;
  showCoverage?: boolean;
  /** Which coverage radius to draw. The rings are exported per radius. */
  radiusM?: number;
  countries?: string[] | null;
  showCompanies?: boolean;
  /** Selected power band indices. The same bands the coverage uses. */
  powerBands?: { lo: number; hi: number | null }[];
  bands?: number[];
  tiers: Tier[];
  access: AccessClass[];
  hgvOnly: boolean;
  focus: { id: string; lon: number; lat: number } | null;
  onSelect: (id: string) => void;
  /** TENtec / AFIR truck sites. A second source beside the OSM depots. */
  afirHdv?: { type: string; features: unknown[] } | null;
  /** TENtec / AFIR light-duty sites, compact rows, fetched on demand. */
  afirLdv?: { meta: { countries: string[] }; rows: number[][] } | null;
  /** Which AFIR class to draw. The OSM depot layer is truck-only either way. */
  vehicleClass?: "all" | "hdv" | "ldv";
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
  coverage,
  showCoverage = false,
  radiusM = 500,
  countries,
  companies = null,
  showCompanies = true,
  powerBands = [],
  bands = [],
  tiers,
  access,
  hgvOnly,
  focus,
  onSelect,
  afirHdv = null,
  afirLdv = null,
  vehicleClass = "hdv",
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MlMap | null>(null);
  const popupRef = useRef<maplibregl.Popup | null>(null);
  const tiersRef = useRef<Tier[]>(tiers);
  tiersRef.current = tiers;
  // The coverage layers are created inside map.once("load") when the style is
  // not ready yet, so the radius must be readable at creation time and not only
  // in the effect that runs later. Without this the creation happened with no
  // filter and all four radii drew.
  const radiusRef = useRef(radiusM);
  radiusRef.current = radiusM;

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASEMAP_STYLE,
      bounds: EUROPE_BOUNDS,
      fitBoundsOptions: { padding: 28 },
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    mapRef.current = map;
    // Read-only handle for QA and for anyone debugging in the console. Without
    // it the only way to check which radius drew was to count amber pixels,
    // and the depot markers are amber too, so that measured the markers.
    (window as unknown as { __depotMap?: MlMap }).__depotMap = map;
    // exposed for diagnostics: the basemap can silently fail to paint
    if (typeof window !== "undefined") (window as any).__depotMap = map;
    (window as any).__mapErrors = [];
    map.on("error", (e: any) => {
      (window as any).__mapErrors.push(String((e && e.error && e.error.message) || e));
    });

    // MapLibre measures the container once, at construction. When the sidebar
    // grows (extra filter rows), the map keeps a stale canvas size and paints
    // nothing until something forces a resize — the page then reads as "blank
    // map while the zoom controls still render". Observe the container instead.
    const container = containerRef.current;
    const ro = new ResizeObserver(() => map.resize());
    ro.observe(container);
    map.once("load", () => map.resize());
    // Fonts and the first paint can land after load; one deferred resize is cheap.
    const t = window.setTimeout(() => map.resize(), 400);

    return () => {
      window.clearTimeout(t);
      ro.disconnect();
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
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 1.2, 7, 2.0, 11, 3.2],
          "circle-color": ["match", ["get", "kind"], "carrier", "#0f766e", "#64748b"],
          "circle-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.22, 8, 0.35, 11, 0.5],
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

  // TENtec / AFIR layers. Drawn under the depot markers: the depot layer is the
  // product's own finding, the AFIR layer is the official context around it.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const ensure = () => {
      const wantHdv = vehicleClass !== "ldv";
      const wantLdv = vehicleClass !== "hdv";

      if (afirHdv && !map.getSource("afir-hdv")) {
        map.addSource("afir-hdv", { type: "geojson", data: afirHdv as never });
        map.addLayer({
          id: "afir-hdv",
          type: "circle",
          source: "afir-hdv",
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 1.8, 7, 3.2, 11, 6],
            // Violet, not amber: the coverage rings are amber (#f59e0b), and
            // drawing the AFIR sites in the same hue made the two layers
            // indistinguishable on the map and in pixel checks. One colour, one
            // meaning.
            "circle-color": "#7c3aed",
            "circle-opacity": 0.6,
            "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 3, 0, 7, 0.4, 11, 0.9],
            "circle-stroke-color": "#5b21b6",
            "circle-stroke-opacity": 0.7,
          },
        });
      }
      // The compact LDV rows become a GeoJSON source here, not on the server:
      // a flat array is a quarter of the bytes and this runs once, on toggle.
      if (afirLdv && !map.getSource("afir-ldv")) {
        const cc = afirLdv.meta.countries;
        map.addSource("afir-ldv", {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: afirLdv.rows.map((r: number[]) => ({
              type: "Feature",
              geometry: { type: "Point", coordinates: [r[0], r[1]] },
              properties: { kw: r[2], cc: cc[r[3]] ?? null, nc: r[4] },
            })),
          } as never,
        });
        map.addLayer({
          id: "afir-ldv",
          type: "circle",
          source: "afir-ldv",
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 1.1, 7, 1.6, 11, 2.6],
            "circle-color": "#38bdf8",
            "circle-opacity": 0.35,
            "circle-stroke-width": 0,
          },
        });
      }

      for (const [id, on] of [["afir-hdv", wantHdv], ["afir-ldv", wantLdv]] as const) {
        if (map.getLayer(id)) {
          map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
        }
      }
    };
    if (map.isStyleLoaded()) ensure();
    else map.once("load", ensure);
  }, [afirHdv, afirLdv, vehicleClass]);

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
              ["zoom"],
              3,
              ["interpolate",
                ["linear"],
                ["coalesce", ["get", "powerKw"], 0],
                0, 2.2,
                400, 2.8,
                1200, 3.6,
                3000, 4.6,
              ],
              10,
              ["interpolate",
                ["linear"],
                ["coalesce", ["get", "powerKw"], 0],
                0, 5,
                400, 7,
                1200, 10,
                3000, 14,
              ],
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
            // The white ring is what made Benelux unreadable: 3,382 sites at
            // Europe zoom, each a 5-14 px circle with a 1.2 px white edge, so
            // the rings merged into one blob and buried the basemap labels.
            // The edge is now proportional to the circle and fades out when
            // zoomed out, so a dense cluster reads as a cluster.
            "circle-stroke-color": "#ffffff",
            "circle-stroke-width": [
              "interpolate",
              ["linear"],
              ["zoom"],
              3,
              0,
              6,
              0.6,
              10,
              1.2,
            ],
            "circle-stroke-opacity": [
              "interpolate",
              ["linear"],
              ["zoom"],
              3,
              0,
              5,
              0.5,
              8,
              1,
            ],
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

  // Coverage rings. Added under the depot dots so the site markers stay
  // readable, and toggled by visibility instead of being removed and re-added.
  // The file holds rings for several radii; the map shows one radius at a time,
  // the way the pakketpuntenviewer does.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !coverage) return;
    const ensure = () => {
      const existing = map.getSource("coverage");
      if (existing) {
        // The rings are rebuilt on every radius and filter change now, so the
        // source has to take the new data. Returning here would keep the first
        // frame on screen forever.
        (existing as maplibregl.GeoJSONSource).setData(coverage as never);
        return;
      }
      map.addSource("coverage", { type: "geojson", data: coverage as never });
      const before = map.getLayer("depots") ? "depots" : undefined;
      const radiusFilter = ["==", ["get", "radiusM"], radiusRef.current];
      map.addLayer(
        {
          id: "coverage",
          type: "fill",
          source: "coverage",
          filter: radiusFilter as never,
          paint: { "fill-color": "#0d9488", "fill-opacity": 0.12 },
        },
        before,
      );
      map.addLayer(
        {
          id: "coverage-line",
          type: "line",
          source: "coverage",
          filter: radiusFilter as never,
          paint: { "line-color": "#0f766e", "line-width": 0.8, "line-opacity": 0.5 },
        },
        before,
      );
    };
    if (map.isStyleLoaded()) ensure();
    else map.once("load", ensure);
  }, [coverage]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    for (const id of ["coverage", "coverage-line"]) {
      if (map.getLayer(id)) {
        map.setLayoutProperty(id, "visibility", showCoverage ? "visible" : "none");
      }
    }
  }, [showCoverage, coverage]);

  // Only the rings of the selected radius are drawn. If the layer is not there
  // yet, set it as soon as the style settles instead of returning and never
  // applying it, which is what left all four radii on screen.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      const f = ["==", ["get", "radiusM"], radiusM] as never;
      if (map.getLayer("coverage")) map.setFilter("coverage", f);
      if (map.getLayer("coverage-line")) map.setFilter("coverage-line", f);
    };
    if (map.getLayer("coverage")) apply();
    else map.once("idle", apply);
  }, [radiusM, coverage]);

  // Tier, HGV, power and access filters are client-side, so the chips work without a rebuild.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer("depots")) return;
    const filters: unknown[] = [["in", ["get", "tier"], ["literal", tiers]]];
    // Missing accessClass (older data) counts as "unknown", matching the sidebar list.
    filters.push(["in", ["coalesce", ["get", "accessClass"], "unknown"], ["literal", access]]);
    if (hgvOnly) filters.push(["==", ["get", "hgv"], true]);
    // Power as bands. A charger with no published power is band 0, so it is
    // included only when band 0 is selected, never silently.
    if (powerBands.length && bands.length && bands.length < powerBands.length) {
      const ranges = bands.map((i) => powerBands[i]).filter(Boolean);
      const anyOf: unknown[] = ["any"];
      for (const b of ranges) {
        const parts: unknown[] = ["all"];
        parts.push([">=", ["coalesce", ["get", "powerKw"], 0], b.lo]);
        if (b.hi !== null) parts.push(["<", ["coalesce", ["get", "powerKw"], 0], b.hi]);
        anyOf.push(parts);
      }
      if (anyOf.length > 1) filters.push(anyOf);
    }
    // Country selection from the sidebar; no selection means every country.
    if (countries) {
      filters.push(["in", ["coalesce", ["get", "country"], "?"], ["literal", countries]]);
    }
    map.setFilter("depots", ["all", ...filters] as never);
  }, [tiers, access, hgvOnly, bands, powerBands, countries, depots]);

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
