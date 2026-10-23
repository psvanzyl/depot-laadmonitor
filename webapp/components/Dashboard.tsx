"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import {
  CHANNEL_LABEL,
  TIER_COLOR,
  TIER_HINT,
  TIER_LABEL,
  type Depots,
  type Summary,
  type Tier,
} from "@/lib/types";

const MapView = dynamic(() => import("@/components/MapView"), {
  ssr: false,
  loading: () => (
    <div className="absolute inset-0 flex items-center justify-center bg-slate-100 text-slate-500">
      Kaart laden…
    </div>
  ),
});

const nf = new Intl.NumberFormat("nl-NL");
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const dataUrl = (file: string) => `${BASE_PATH}/data/${file}`;
const ALL_TIERS: Tier[] = ["A", "B", "C"];

export default function Dashboard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [depots, setDepots] = useState<Depots | null>(null);
  const [tiers, setTiers] = useState<Tier[]>(ALL_TIERS);
  const [hgvOnly, setHgvOnly] = useState(false);
  const [companies, setCompanies] = useState<{ type: "FeatureCollection"; features: unknown[] } | null>(null);
  const [showCompanies, setShowCompanies] = useState(true);
  const [minKw, setMinKw] = useState(0);
  const [focus, setFocus] = useState<{ id: string; lon: number; lat: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(dataUrl("summary.json"))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`summary ${r.status}`))))
      .then((d: Summary) => setSummary(d))
      .catch((e) => setError(String(e)));
    fetch(dataUrl("companies.geojson"))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`companies ${r.status}`))))
      .then((d) => setCompanies(d))
      .catch(() => setCompanies(null)); // base layer is optional context, not an error
    fetch(dataUrl("depots.geojson"))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`depots ${r.status}`))))
      .then((d: Depots) => setDepots(d))
      .catch((e) => setError(String(e)));
  }, []);

  const all = depots?.features ?? [];
  const visible = useMemo(
    () =>
      all
        .filter((f) => tiers.includes(f.properties.tier))
        .filter((f) => (hgvOnly ? f.properties.hgv : true))
        .filter((f) => (minKw > 0 ? (f.properties.powerKw ?? 0) >= minKw : true))
        .sort(
          (a, b) =>
            "ABC".indexOf(a.properties.tier) - "ABC".indexOf(b.properties.tier) ||
            b.properties.score - a.properties.score,
        ),
    [all, tiers, hgvOnly, minKw],
  );

  const tierCounts = useMemo(() => {
    const c: Record<Tier, number> = { A: 0, B: 0, C: 0 };
    for (const f of all) c[f.properties.tier] += 1;
    return c;
  }, [all]);

  const kwVisible = visible.reduce((s, f) => s + (f.properties.powerKw ?? 0), 0);
  const provinces = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of visible) {
      const p = f.properties.province || "Onbekend";
      c[p] = (c[p] ?? 0) + 1;
    }
    return Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, 8);
  }, [visible]);

  const toggleTier = (t: Tier) =>
    setTiers((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]));

  return (
    <div className="h-screen flex flex-col">
      <header className="bg-white border-b border-slate-200 shadow-sm z-20">
        <div className="px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-2">
          <h1 className="text-lg font-bold text-slate-900 whitespace-nowrap">
            <span className="text-amber-600">⚡</span> Depotlaadpalen
          </h1>
          <p className="text-xs text-slate-500 hidden md:block">
            Private laadlocaties bij logistieke bedrijven in Nederland
          </p>
          {summary && (
            <span className="text-[11px] text-slate-400 ml-auto">
              data: {new Date(summary.generatedAt).toLocaleString("nl-NL")}
            </span>
          )}
        </div>
      </header>

      <div className="flex-1 flex flex-col lg:flex-row overflow-hidden">
        <aside className="lg:w-[380px] lg:shrink-0 bg-slate-50 border-b lg:border-b-0 lg:border-r border-slate-200 overflow-y-auto p-4 space-y-4">
          <section className="bg-white rounded-xl border border-slate-200 p-4 space-y-1">
            <div className="text-4xl font-bold text-amber-700 tabular-nums">
              {depots ? nf.format(visible.length) : "…"}
            </div>
            <p className="text-sm text-slate-600">
              private depotlaadlocaties met bewijs, van {nf.format(all.length)} gescoorde
              locaties.
            </p>
            <p className="text-xs text-slate-500">
              {nf.format(Math.round(kwVisible))} kW opgeteld bij de locaties waar een
              vermogen bekend is — nooit geschat.
            </p>
          </section>

          <section className="bg-white rounded-xl border border-slate-200 p-4 space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Zekerheid
            </h2>
            <div className="flex flex-wrap gap-2">
              {ALL_TIERS.map((t) => {
                const on = tiers.includes(t);
                return (
                  <button
                    key={t}
                    onClick={() => toggleTier(t)}
                    title={TIER_HINT[t]}
                    className={`px-2.5 py-1 rounded-md border text-xs font-medium transition ${
                      on
                        ? "text-white border-transparent"
                        : "bg-white border-slate-300 text-slate-500 hover:border-amber-400"
                    }`}
                    style={on ? { backgroundColor: TIER_COLOR[t] } : undefined}
                  >
                    {t} · {nf.format(tierCounts[t])}
                  </button>
                );
              })}
              <button
                onClick={() => setHgvOnly((v) => !v)}
                title="Alleen locaties waar het bewijs vrachtwagens noemt"
                className={`px-2.5 py-1 rounded-md border text-xs font-medium transition ${
                  hgvOnly
                    ? "bg-slate-900 border-slate-900 text-white"
                    : "bg-white border-slate-300 text-slate-500 hover:border-amber-400"
                }`}
              >
                HGV
              </button>
              <button
                onClick={() => setShowCompanies((v) => !v)}
                title="Toon alle transportbedrijven (basislaag) onder de vondsten"
                className={`px-2.5 py-1 rounded-md border text-xs font-medium transition ${
                  showCompanies
                    ? "bg-teal-700 border-teal-700 text-white"
                    : "bg-white border-slate-300 text-slate-500 hover:border-amber-400"
                }`}
              >
                Bedrijven
              </button>
              <button
                onClick={() => setMinKw((v) => (v === 0 ? 50 : 0))}
                title="Alleen locaties met een bekend vermogen van 50 kW of meer"
                className={`px-2.5 py-1 rounded-md border text-xs font-medium transition ${
                  minKw > 0
                    ? "bg-slate-900 border-slate-900 text-white"
                    : "bg-white border-slate-300 text-slate-500 hover:border-amber-400"
                }`}
              >
                50+ kW
              </button>
            </div>
            <div className="space-y-1 pt-1">
              {ALL_TIERS.map((t) => (
                <p key={t} className="text-[11px] leading-snug text-slate-500">
                  <span
                    className="inline-block w-2 h-2 rounded-full mr-1.5 align-middle"
                    style={{ backgroundColor: TIER_COLOR[t] }}
                  />
                  <strong className="text-slate-700">{TIER_LABEL[t]}</strong> — {TIER_HINT[t]}
                </p>
              ))}
            </div>
          </section>

          <section className="bg-white rounded-xl border border-slate-200 p-4">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              Locaties ({nf.format(visible.length)})
            </h2>
            <ul className="divide-y divide-slate-100 max-h-[42vh] overflow-y-auto -mx-1">
              {visible.slice(0, 150).map((f) => {
                const p = f.properties;
                const [lon, lat] = f.geometry.coordinates as [number, number];
                return (
                  <li key={p.id}>
                    <button
                      onClick={() => setFocus({ id: p.id, lon, lat })}
                      className="w-full text-left px-1 py-2 hover:bg-amber-50 rounded-md transition"
                    >
                      <div className="flex items-baseline gap-2">
                        <span
                          className="inline-block w-2 h-2 rounded-full shrink-0"
                          style={{ backgroundColor: TIER_COLOR[p.tier] }}
                        />
                        <span className="text-sm font-medium text-slate-800 truncate">
                          {p.operator}
                        </span>
                        <span className="text-[11px] text-slate-400 ml-auto shrink-0 tabular-nums">
                          {p.score.toFixed(2)}
                        </span>
                      </div>
                      <div className="text-[11px] text-slate-500 pl-4 truncate">
                        {p.city || p.postcode || "plaats onbekend"}
                        {p.powerKw ? ` · ${nf.format(p.powerKw)} kW` : ""}
                        {(p as { locationBasis?: string }).locationBasis === "city"
                          ? " · bij benadering"
                          : ""}
                        {p.hgv ? " · HGV" : ""}
                        {" · "}
                        {p.evidenceCount} bron{p.evidenceCount === 1 ? "" : "nen"}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
            {visible.length > 150 && (
              <p className="text-[11px] text-slate-400 mt-2">
                Eerste 150 van {nf.format(visible.length)} — gebruik de kaart of de
                zekerheidsfilters om te verfijnen.
              </p>
            )}
          </section>

          {provinces.length > 0 && (
            <section className="bg-white rounded-xl border border-slate-200 p-4">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                Per provincie
              </h2>
              <table className="w-full text-xs">
                <tbody>
                  {provinces.map(([name, n]) => (
                    <tr key={name} className="border-t border-slate-100 first:border-0">
                      <td className="py-1.5 text-slate-700">{name}</td>
                      <td className="py-1.5 text-right tabular-nums text-slate-600">
                        {nf.format(n)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {error && (
            <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">
              Fout bij laden: {error}
            </p>
          )}

          <p className="text-[11px] leading-relaxed text-slate-400 px-1">
            Private laadlocaties hebben geen openbaar register. Elke locatie is afgeleid
            uit indirect bewijs — {Object.values(CHANNEL_LABEL).join(", ")} — en elke rij
            linkt naar de bron. Publieke laadpleinen worden juist verworpen.
            {summary?.sites.withoutCoords
              ? ` ${summary.sites.withoutCoords} locatie(s) konden niet op de kaart (geen coördinaten).`
              : ""}
          </p>
        </aside>

        <main className="relative flex-1 min-h-[420px]">
          <MapView
            depots={depots}
            companies={companies}
            showCompanies={showCompanies}
            minKw={minKw}
            tiers={tiers}
            hgvOnly={hgvOnly}
            focus={focus}
            onSelect={(id) => {
              const f = all.find((x) => x.properties.id === id);
              if (!f) return;
              const [lon, lat] = f.geometry.coordinates as [number, number];
              setFocus({ id, lon, lat });
            }}
          />
        </main>
      </div>
    </div>
  );
}
