"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { DexLogo } from "@/components/DexLogo";
import { MapBoundary } from "@/components/MapBoundary";
import { LANGS, isLang, makeT, type Lang } from "@/lib/i18n";
import {
  TIER_COLOR,
  type AccessClass,
  type Depots,
  type Summary,
  type Tier,
} from "@/lib/types";

const COUNTRY_NAME: Record<string, string> = {
  NL: "Nederland",
  DE: "Duitsland",
  CH: "Zwitserland",
  ES: "Spanje",
  IT: "Italië",
  AT: "Oostenrijk",
  BE: "België",
  LU: "Luxemburg",
  LI: "Liechtenstein",
  FR: "Frankrijk",
  DK: "Denemarken",
  SE: "Zweden",
  NO: "Noorwegen",
  FI: "Finland",
  IS: "IJsland",
  PL: "Polen",
  CZ: "Tsjechië",
  SK: "Slowakije",
  HU: "Hongarije",
  SI: "Slovenië",
  HR: "Kroatië",
  IE: "Ierland",
  GB: "Verenigd Koninkrijk",
  EE: "Estland",
  LV: "Letland",
  LT: "Litouwen",
  RO: "Roemenië",
  BG: "Bulgarije",
  GR: "Griekenland",
  PT: "Portugal",
  RS: "Servië",
  BA: "Bosnië-Herzegovina",
  ME: "Montenegro",
  MK: "Noord-Macedonië",
  AL: "Albanië",
  CY: "Cyprus",
  MT: "Malta",
  AD: "Andorra",
  MC: "Monaco",
  SM: "San Marino",
  VA: "Vaticaanstad",
  XK: "Kosovo",
};

const MapView = dynamic(() => import("@/components/MapView"), {
  ssr: false,
  loading: () => (
    <div className="absolute inset-0 flex items-center justify-center bg-slate-100 text-slate-500">
      Kaart laden…
    </div>
  ),
});

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const dataUrl = (file: string) => `${BASE_PATH}/data/${file}`;
const ALL_TIERS: Tier[] = ["A", "B", "C"];
const ALL_ACCESS: AccessClass[] = ["public", "semi_public", "private", "unknown"];
const accessOf = (p: { accessClass?: AccessClass }): AccessClass =>
  p.accessClass ?? "unknown";
const POWER_STEPS = [0, 50, 150, 350];
/** Radii the map can draw, the way the pakketpuntenviewer lets you pick one. */
const RADII = [500, 1000, 2000, 5000];

type Coverage = {
  radiusM: number;
  rings: number;
  ringsHgv: number;
  areaKm2Hgv: number;
  companiesTotal: number;
  companiesCovered: { "500m": number; "2km": number; "5km": number };
  pctCovered500m: number | null;
  pctCovered5km: number | null;
  chargersHgvNow: number;
  chargersNeeded: number | null;
  assumption: string;
  method: string;
  countries?: {
    country: string;
    companies: number;
    chargers: number;
    chargersHgv: number;
    covered500m: number;
    covered5km: number;
    pctCovered500m: number | null;
    pctCovered5km: number | null;
    chargersNeeded: number | null;
  }[];
};

export default function Dashboard() {
  const [lang, setLang] = useState<Lang>("nl");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [depots, setDepots] = useState<Depots | null>(null);
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [coverageGeo, setCoverageGeo] = useState<{ type: string; features: unknown[] } | null>(null);
  const [showCoverage, setShowCoverage] = useState(false);
  const [radiusM, setRadiusM] = useState(500);
  const [tiers, setTiers] = useState<Tier[]>(ALL_TIERS);
  const [access, setAccess] = useState<AccessClass[]>(ALL_ACCESS);
  const [countries, setCountries] = useState<string[] | null>(null);
  const [hgvOnly, setHgvOnly] = useState(false);
  const [companies, setCompanies] = useState<{ type: "FeatureCollection"; features: unknown[] } | null>(null);
  const [showCompanies, setShowCompanies] = useState(true);
  const [minKw, setMinKw] = useState(0);
  const [focus, setFocus] = useState<{ id: string; lon: number; lat: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAllCountries, setShowAllCountries] = useState(false);

  // Language: URL first (so a shared link wins), then localStorage.
  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get("lang");
    const stored = window.localStorage.getItem("dex-lang");
    const next = isLang(fromUrl) ? fromUrl : isLang(stored) ? stored : "nl";
    setLang(next);
  }, []);

  const changeLang = (next: Lang) => {
    setLang(next);
    window.localStorage.setItem("dex-lang", next);
    const url = new URL(window.location.href);
    url.searchParams.set("lang", next);
    window.history.replaceState({}, "", url);
  };

  // The <html lang> attribute is what a screen reader uses to pick a voice,
  // and what a search engine reads. It was hardcoded to "nl" in the layout,
  // so a German page announced Dutch. The layout cannot know the language
  // (it is chosen in the client, from the URL or localStorage), so the
  // attribute is set here, where the choice is made.
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const t = useMemo(() => makeT(lang), [lang]);
  const nf = useMemo(
    () => new Intl.NumberFormat(lang === "nl" ? "nl-NL" : lang === "de" ? "de-DE" : lang === "fr" ? "fr-FR" : "en-GB"),
    [lang],
  );

  useEffect(() => {
    fetch(dataUrl("summary.json"))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`summary ${r.status}`))))
      .then((d: Summary) => setSummary(d))
      .catch((e) => setError(String(e)));
    fetch(dataUrl("companies.geojson"))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`companies ${r.status}`))))
      .then((d) => setCompanies(d))
      .catch(() => setCompanies(null));
    fetch(dataUrl("coverage.json"))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`coverage ${r.status}`))))
      .then((d: Coverage) => setCoverage(d))
      .catch(() => setCoverage(null));
    fetch(dataUrl("coverage.geojson"))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`rings ${r.status}`))))
      .then((d) => setCoverageGeo(d))
      .catch(() => setCoverageGeo(null));
    fetch(dataUrl("depots.geojson"))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`depots ${r.status}`))))
      .then((d: Depots) => setDepots(d))
      .catch((e) => setError(String(e)));
  }, []);

  const all = depots?.features ?? [];

  const countryCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of all) {
      const cc = f.properties.country || "?";
      c[cc] = (c[cc] ?? 0) + 1;
    }
    return Object.entries(c).sort((a, b) => b[1] - a[1]);
  }, [all]);

  const visible = useMemo(
    () =>
      all
        .filter((f) => tiers.includes(f.properties.tier))
        .filter((f) => access.includes(accessOf(f.properties)))
        .filter((f) => (hgvOnly ? f.properties.hgv : true))
        .filter((f) => (minKw > 0 ? (f.properties.powerKw ?? 0) >= minKw : true))
        .filter((f) => (countries ? countries.includes(f.properties.country || "?") : true))
        .sort(
          (a, b) =>
            "ABC".indexOf(a.properties.tier) - "ABC".indexOf(b.properties.tier) ||
            b.properties.score - a.properties.score,
        ),
    [all, tiers, access, hgvOnly, minKw, countries],
  );

  const tierCounts = useMemo(() => {
    const c: Record<Tier, number> = { A: 0, B: 0, C: 0 };
    for (const f of all) c[f.properties.tier] += 1;
    return c;
  }, [all]);

  const accessCounts = useMemo(() => {
    const c: Record<AccessClass, number> = { public: 0, semi_public: 0, private: 0, unknown: 0 };
    for (const f of all) c[accessOf(f.properties)] += 1;
    return c;
  }, [all]);

  const kwVisible = visible.reduce((s, f) => s + (f.properties.powerKw ?? 0), 0);

  // Region table: Dutch provinces for NL rows, country names for the rest.
  const regions = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of visible) {
      const cc = f.properties.country;
      const p =
        f.properties.province ||
        (cc && cc !== "NL" ? COUNTRY_NAME[cc] ?? cc : "Onbekend");
      c[p] = (c[p] ?? 0) + 1;
    }
    return Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, 10);
  }, [visible]);

  // Per-country fleet estimate, from the coverage export.
  const countryCoverage = useMemo(() => {
    const rows = coverage?.countries ?? [];
    const selected = countries ? new Set(countries) : null;
    return rows
      .filter((r) => (selected ? selected.has(r.country) : true))
      .filter((r) => r.companies > 0 || r.chargersHgv > 0)
      .slice(0, 30);
  }, [coverage, countries]);

  const toggleTier = (x: Tier) =>
    setTiers((cur) => (cur.includes(x) ? cur.filter((y) => y !== x) : [...cur, x]));
  const toggleAccess = (x: AccessClass) =>
    setAccess((cur) => (cur.includes(x) ? cur.filter((y) => y !== x) : [...cur, x]));
  const toggleCountry = (cc: string) => {
    const base = countries ?? countryCounts.map(([k]) => k);
    setCountries(base.includes(cc) ? base.filter((y) => y !== cc) : [...base, cc]);
  };
  const countryOn = (cc: string) => (countries ? countries.includes(cc) : true);

  const accessText = (a: AccessClass) =>
    ({
      public: t("accessPublic"),
      semi_public: t("accessSemi"),
      private: t("accessPrivate"),
      unknown: t("accessUnknown"),
    })[a];
  const tierText = (x: Tier) =>
    ({ A: t("tierA"), B: t("tierB"), C: t("tierC") })[x];

  const countryName = (cc: string) => COUNTRY_NAME[cc] ?? cc;
  const shownCountries = showAllCountries ? countryCounts : countryCounts.slice(0, 12);

  return (
    <div className="min-h-screen lg:h-screen flex flex-col">
      <header className="bg-white border-b border-slate-200 shadow-sm z-20">
        <div className="px-4 py-3 flex flex-wrap items-center gap-x-4 gap-y-2">
          <DexLogo />
          <p className="text-xs text-slate-500 hidden md:block">{t("tagline")}</p>
          <div className="ml-auto flex items-center gap-3">
            {summary && (
              <span className="text-[11px] text-slate-400 hidden sm:block">
                {t("dataAt")}: {new Date(summary.generatedAt).toLocaleString(lang)}
              </span>
            )}
            <label className="sr-only" htmlFor="lang">
              {t("langLabel")}
            </label>
            <select
              id="lang"
              value={lang}
              onChange={(e) => changeLang(e.target.value as Lang)}
              className="text-xs border border-slate-300 rounded-md px-2 py-1 bg-white text-slate-700"
            >
              {LANGS.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </header>

      <div className="flex-1 flex flex-col lg:flex-row lg:overflow-hidden">
        <aside className="order-last lg:order-none lg:w-[400px] lg:shrink-0 lg:min-h-0 bg-slate-50 border-b lg:border-b-0 lg:border-r border-slate-200 overflow-visible lg:overflow-y-auto p-4 space-y-4">
          <section className="bg-white rounded-xl border border-slate-200 p-4 space-y-1">
            <div className="text-4xl font-bold text-amber-700 tabular-nums">
              {depots ? nf.format(visible.length) : "…"}
            </div>
            <p className="text-sm text-slate-600">
              {t("sitesWithEvidence")} {nf.format(all.length)} {t("scoredSites")}
            </p>
            <p className="text-xs text-slate-500">
              {nf.format(Math.round(kwVisible))} {t("kwSummed")}
            </p>
          </section>

          {coverage && (
            <section className="bg-white rounded-xl border border-amber-200 p-4 space-y-3">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-xs font-semibold uppercase tracking-wide text-amber-700">
                  {t("coverageTitle")}
                </h2>
                <button
                  onClick={() => setShowCoverage((v) => !v)}
                  title={t("coverageHint")}
                  aria-pressed={showCoverage}
                  className={`px-2.5 py-1 rounded-md border text-xs font-medium transition ${
                    showCoverage
                      ? "bg-amber-600 border-amber-600 text-white"
                      : "bg-white border-amber-300 text-amber-700 hover:border-amber-500"
                  }`}
                >
                  {showCoverage ? t("on") : t("off")}
                </button>
              </div>

              {/* Radius picker, the way the pakketpuntenviewer does it. */}
              <div className="space-y-1">
                <div className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
                  {t("radius")}
                </div>
                <div className="flex flex-wrap gap-2">
                  {RADII.map((r) => (
                    <button
                      key={r}
                      onClick={() => setRadiusM(r)}
                      aria-pressed={radiusM === r}
                      className={`px-2.5 py-1 rounded-md border text-xs font-medium tabular-nums transition ${
                        radiusM === r
                          ? "bg-amber-600 border-amber-600 text-white"
                          : "bg-white border-slate-300 text-slate-500 hover:border-amber-400"
                      }`}
                    >
                      {r >= 1000 ? `${r / 1000} km` : `${r} m`}
                    </button>
                  ))}
                </div>
              </div>

              <dl className="grid grid-cols-2 gap-2 text-xs">
                <div>
                  <dt className="text-slate-500">{t("within500")}</dt>
                  <dd className="text-lg font-bold text-slate-800 tabular-nums">
                    {coverage.pctCovered500m?.toFixed(1)}%
                  </dd>
                  <dd className="text-[11px] text-slate-400 tabular-nums">
                    {nf.format(coverage.companiesCovered["500m"])} / {nf.format(coverage.companiesTotal)}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">{t("within5km")}</dt>
                  <dd className="text-lg font-bold text-slate-800 tabular-nums">
                    {coverage.pctCovered5km?.toFixed(1)}%
                  </dd>
                  <dd className="text-[11px] text-slate-400 tabular-nums">
                    {nf.format(coverage.companiesCovered["5km"])} / {nf.format(coverage.companiesTotal)}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">{t("chargersNow")}</dt>
                  <dd className="text-lg font-bold text-slate-800 tabular-nums">
                    {nf.format(coverage.chargersHgvNow)}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">{t("chargersNeeded")}</dt>
                  <dd className="text-lg font-bold text-amber-700 tabular-nums">
                    {coverage.chargersNeeded ? nf.format(coverage.chargersNeeded) : "—"}
                  </dd>
                </div>
              </dl>
              <p className="text-[11px] leading-snug text-slate-400">
                {nf.format(coverage.ringsHgv)} {t("ringsShown")} · {nf.format(coverage.areaKm2Hgv)} km² ·{" "}
                {t("assumption")}
              </p>
            </section>
          )}

          {/* Per-country fleet estimate: how many chargers each country's
              fleet needs, based on the density that country's existing
              chargers already reach. */}
          {countryCoverage.length > 0 && (
            <section className="bg-white rounded-xl border border-slate-200 p-4">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                {t("fleetByCountry")}
              </h2>
              <div className="overflow-x-auto -mx-1 px-1">
                {/* min-w forces a horizontal scrollbar rather than squeezing the
                    columns: at 332 px of card width the four columns do not
                    fit, and squeezed numbers get clipped at the card edge. */}
                <table className="w-full text-xs min-w-[300px] table-fixed">
                  <thead>
                    <tr className="text-slate-400 border-b border-slate-100">
                      <th className="text-left font-medium py-1 w-[42%]">{t("country")}</th>
                      <th className="text-right font-medium py-1 w-[20%]">{t("chargersNow")}</th>
                      <th className="text-right font-medium py-1 w-[16%]">500 m</th>
                      <th className="text-right font-medium py-1 w-[22%]">{t("chargersNeeded")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {countryCoverage.map((r) => (
                      <tr key={r.country} className="border-t border-slate-100">
                        <td className="py-1.5 pr-1 text-slate-700 truncate">{countryName(r.country)}</td>
                        <td className="py-1.5 text-right tabular-nums text-slate-600">
                          {nf.format(r.chargersHgv)}
                        </td>
                        <td className="py-1.5 text-right tabular-nums text-slate-600">
                          {r.pctCovered500m !== null ? `${r.pctCovered500m}%` : "—"}
                        </td>
                        <td className="py-1.5 text-right tabular-nums font-semibold text-amber-700">
                          {r.chargersNeeded !== null ? nf.format(r.chargersNeeded) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-[11px] leading-snug text-slate-400 mt-2">
                {t("fleetByCountryHint")}
              </p>
            </section>
          )}

          <section className="bg-white rounded-xl border border-slate-200 p-4 space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {t("certainty")}
            </h2>
            <div className="flex flex-wrap gap-2">
              {ALL_TIERS.map((x) => {
                const on = tiers.includes(x);
                return (
                  <button
                    key={x}
                    onClick={() => toggleTier(x)}
                    title={t(`tierHint${x}`)}
                    className={`px-2.5 py-1 rounded-md border text-xs font-medium transition ${
                      on
                        ? "text-white border-transparent"
                        : "bg-white border-slate-300 text-slate-500 hover:border-amber-400"
                    }`}
                    style={on ? { backgroundColor: TIER_COLOR[x] } : undefined}
                  >
                    {x} · {nf.format(tierCounts[x])}
                  </button>
                );
              })}
              <button
                onClick={() => setHgvOnly((v) => !v)}
                title={t("hgvHint")}
                aria-pressed={hgvOnly}
                className={`px-2.5 py-1 rounded-md border text-xs font-medium transition ${
                  hgvOnly
                    ? "bg-slate-900 border-slate-900 text-white"
                    : "bg-white border-slate-300 text-slate-500 hover:border-amber-400"
                }`}
              >
                {t("hgv")}
              </button>
              <button
                onClick={() => setShowCompanies((v) => !v)}
                title={t("companiesHint")}
                aria-pressed={showCompanies}
                className={`px-2.5 py-1 rounded-md border text-xs font-medium transition ${
                  showCompanies
                    ? "bg-teal-700 border-teal-700 text-white"
                    : "bg-white border-slate-300 text-slate-500 hover:border-amber-400"
                }`}
              >
                {t("companies")}
              </button>
            </div>
            <div className="space-y-1 pt-1">
              {ALL_TIERS.map((x) => (
                <p key={x} className="text-[11px] leading-snug text-slate-500">
                  <span
                    className="inline-block w-2 h-2 rounded-full mr-1.5 align-middle"
                    style={{ backgroundColor: TIER_COLOR[x] }}
                  />
                  <strong className="text-slate-700">{tierText(x)}</strong> — {t(`tierHint${x}`)}
                </p>
              ))}
            </div>
          </section>

          {/* Access class: private / semi-public / public. */}
          <section className="bg-white rounded-xl border border-slate-200 p-4 space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {t("access")}
            </h2>
            <div className="flex flex-wrap gap-2">
              {ALL_ACCESS.map((a) => {
                const on = access.includes(a);
                return (
                  <button
                    key={a}
                    onClick={() => toggleAccess(a)}
                    title={t(`accessHint${a === "semi_public" ? "Semi" : a === "public" ? "Public" : a === "private" ? "Private" : "Unknown"}`)}
                    aria-pressed={on}
                    className={`px-2.5 py-1 rounded-md border text-xs font-medium transition ${
                      on
                        ? "bg-slate-900 border-slate-900 text-white"
                        : "bg-white border-slate-300 text-slate-500 hover:border-amber-400"
                    }`}
                  >
                    {accessText(a)} · {nf.format(accessCounts[a])}
                  </button>
                );
              })}
            </div>
          </section>

          {/* Power in kW. */}
          <section className="bg-white rounded-xl border border-slate-200 p-4 space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {t("power")}
            </h2>
            <div className="flex flex-wrap gap-2">
              {POWER_STEPS.map((step) => (
                <button
                  key={step}
                  onClick={() => setMinKw(step)}
                  aria-pressed={minKw === step}
                  className={`px-2.5 py-1 rounded-md border text-xs font-medium transition ${
                    minKw === step
                      ? "bg-slate-900 border-slate-900 text-white"
                      : "bg-white border-slate-300 text-slate-500 hover:border-amber-400"
                  }`}
                >
                  {step === 0 ? `${t("power")}: −` : `${step}+ kW`}
                </button>
              ))}
            </div>
          </section>

          {/* Country filter. */}
          <section className="bg-white rounded-xl border border-slate-200 p-4 space-y-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              {t("country")}
            </h2>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => setCountries(null)}
                aria-pressed={countries === null}
                className={`px-2.5 py-1 rounded-md border text-xs font-medium transition ${
                  countries === null
                    ? "bg-slate-900 border-slate-900 text-white"
                    : "bg-white border-slate-300 text-slate-500 hover:border-amber-400"
                }`}
              >
                {t("allCountries")}
              </button>
              {shownCountries.map(([cc, n]) => (
                <button
                  key={cc}
                  onClick={() => toggleCountry(cc)}
                  aria-pressed={countryOn(cc)}
                  className={`px-2.5 py-1 rounded-md border text-xs font-medium transition ${
                    countryOn(cc)
                      ? "bg-white border-amber-400 text-slate-700"
                      : "bg-slate-100 border-slate-200 text-slate-400 line-through"
                  }`}
                >
                  {countryName(cc)} · {nf.format(n)}
                </button>
              ))}
            </div>
            {countryCounts.length > 12 && (
              <button
                onClick={() => setShowAllCountries((v) => !v)}
                className="text-[11px] text-amber-700 hover:underline"
              >
                {showAllCountries ? t("fewerCountries") : t("moreCountries")}
              </button>
            )}
          </section>

          <section className="bg-white rounded-xl border border-slate-200 p-4">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
              {t("sites")} ({nf.format(visible.length)})
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
                        {p.city || p.postcode || t("noPlace")}
                        {p.powerKw ? ` · ${nf.format(p.powerKw)} kW` : ""}
                        {(p as { locationBasis?: string }).locationBasis === "city"
                          ? ` · ${t("approx")}`
                          : ""}
                        {p.hgv ? " · HGV" : ""}
                        {accessOf(p) === "unknown" ? "" : ` · ${accessText(accessOf(p))}`}
                        {" · "}
                        {p.evidenceCount}{" "}
                        {p.evidenceCount === 1 ? t("sources") : t("sourcesPlural")}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
            {visible.length > 150 && (
              <p className="text-[11px] text-slate-400 mt-2">
                {t("first150")} {nf.format(visible.length)} — {t("refineHint")}
              </p>
            )}
          </section>

          {regions.length > 0 && (
            <section className="bg-white rounded-xl border border-slate-200 p-4">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">
                {t("perRegion")}
              </h2>
              <table className="w-full text-xs">
                <tbody>
                  {regions.map(([name, n]) => (
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
              {t("loadError")}: {error}
            </p>
          )}

          <p className="text-[11px] leading-relaxed text-slate-400 px-1">
            {t("footer")}
            {summary?.sites.withoutCoords
              ? ` ${summary.sites.withoutCoords} ${t("noCoords")}`
              : ""}
          </p>
        </aside>

        {/* The map comes FIRST on mobile (order-first), because the sidebar
            with its filters and the 150-row site list is 2100+ px tall. With
            the map below it, the map was below the fold on every phone: the
            filters were reachable by scrolling, but the map itself never was.
            On desktop the sidebar keeps its place on the left. */}
        <main className="relative order-first h-[55vh] lg:order-none lg:h-auto lg:flex-1 min-h-[380px]">
          <MapBoundary message={t("mapUnavailable")}>
          <MapView
            depots={depots}
            companies={companies}
            coverage={coverageGeo}
            showCoverage={showCoverage}
            radiusM={radiusM}
            showCompanies={showCompanies}
            minKw={minKw}
            tiers={tiers}
            access={access}
            hgvOnly={hgvOnly}
            countries={countries}
            focus={focus}
            onSelect={(id) => {
              const f = all.find((x) => x.properties.id === id);
              if (!f) return;
              const [lon, lat] = f.geometry.coordinates as [number, number];
              setFocus({ id, lon, lat });
            }}
          />
          </MapBoundary>
        </main>
      </div>
    </div>
  );
}
