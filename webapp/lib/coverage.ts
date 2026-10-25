/**
 * Live coverage calculation for DepotChargeDex.
 *
 *   "With the filters I have set, what share of the transport companies
 *    within range of a charging station is covered?"
 *
 * The radius reaches 20 km now, and that changed the data layout.
 *
 * The old file held every pair within 5 km with a distance on each pair. At
 * 20 km that set is 2,069,191 pairs and about 37 MB of JSON, and asking the
 * database per slider move costs about a second. Neither is a filter.
 *
 * So each radius ships its own file of 32-bit integers:
 *
 *     (chargerIndex << companyBits) | companyIndex
 *
 * The radius is implicit in which file was loaded, so a 1 km file cannot
 * contain a 2 km pair and no distance needs storing. The browser fetches only
 * the selected radius: 0.1 MB at 1 km, 8.3 MB once at 20 km.
 *
 * The count is DISTINCT over companies, never over pairs. A company with four
 * chargers within range is one company. The server runs the same maths at
 * export time and prints it, so the two can be compared.
 */

export type ChargerRow = [
  country: string,
  accessClass: string,
  powerBand: number,
  hgv: number,
  tier: string,
  source: number,
  lon: number,
  lat: number,
];

export interface CoverageMeta {
  radii: number[];
  powerBands: { lo: number; hi: number | null }[];
  accessClasses: string[];
  /** bits reserved for the company index in a packed pair */
  companyBits: number;
  chargers: ChargerRow[];
  companiesByCountry: Record<string, number>;
  companiesTotal: number;
}

export interface CoverageFilter {
  radiusM: number;
  /** null means every country */
  countries: string[] | null;
  /** null means every access class; an empty array means none */
  access: string[] | null;
  /** null means every power band; an empty array means none */
  bands: number[] | null;
  hgvOnly: boolean;
  /** null means both sources; [] means none. 0 = own, 1 = AFIR. */
  sources?: number[] | null;
}

export interface CoverageResult {
  covered: number;
  denominator: number;
  pct: number | null;
  chargers: number;
  coveredUnfiltered: number;
}

export interface CoverageCountryRow {
  country: string;
  covered: number;
  denominator: number;
  pct: number | null;
  chargers: number;
}

const maskFor = (bits: number) => (1 << bits) - 1;

/**
 * Which power band a charger falls in. A charger with no stated power lands in
 * band 0, the same rule the exporter uses, so the map and the count agree.
 */
export function bandFor(
  powerKw: number | null,
  bands: { lo: number; hi: number | null }[],
): number {
  if (powerKw === null || powerKw === undefined) return 0;
  for (let i = 0; i < bands.length; i++) {
    const b = bands[i];
    if (powerKw >= b.lo && (b.hi === null || powerKw < b.hi)) return i;
  }
  return bands.length - 1;
}

/** Chargers whose attributes pass the filter. Shared by both functions. */
function matchingChargers(
  meta: CoverageMeta,
  filter: CoverageFilter,
): { chargers: number; ok: Uint8Array } {
  const accessSet = filter.access ? new Set(filter.access) : null;
  const bandSet = filter.bands ? new Set(filter.bands) : null;
  const countrySet = filter.countries ? new Set(filter.countries) : null;
  const sourceSet =
    filter.sources === undefined || filter.sources === null
      ? null
      : new Set<number>(filter.sources);

  const ok = new Uint8Array(meta.chargers.length);
  let n = 0;
  for (let i = 0; i < meta.chargers.length; i++) {
    const c = meta.chargers[i];
    if (countrySet && !countrySet.has(c[0])) continue;
    if (accessSet && !accessSet.has(c[1])) continue;
    if (bandSet && !bandSet.has(c[2])) continue;
    if (filter.hgvOnly && c[3] !== 1) continue;
    if (sourceSet && !sourceSet.has(c[5])) continue;
    ok[i] = 1;
    n += 1;
  }
  return { chargers: n, ok };
}

export function computeCoverage(
  meta: CoverageMeta,
  pairs: Uint32Array,
  filter: CoverageFilter,
): CoverageResult {
  const { chargers: nChargers, ok } = matchingChargers(meta, filter);
  const countrySet = filter.countries ? new Set(filter.countries) : null;
  const mask = maskFor(meta.companyBits);

  const covered = new Set<number>();
  const unfiltered = new Set<number>();

  for (let i = 0; i < pairs.length; i++) {
    const v = pairs[i];
    const ci = v >>> meta.companyBits;
    const company = v & mask;
    // Every pair in this file is inside the selected radius, so the unfiltered
    // set ignores the attribute filters but keeps the radius: that is what
    // "companies within range of ANY charger" means.
    unfiltered.add(company);
    if (!ok[ci]) continue;
    covered.add(company);
  }

  // The denominator is every company in the selected countries, covered or not.
  let denominator = 0;
  if (countrySet) {
    for (const cc of countrySet) denominator += meta.companiesByCountry[cc] ?? 0;
  } else {
    denominator = meta.companiesTotal;
  }

  return {
    covered: covered.size,
    denominator,
    pct: denominator > 0 ? Math.round((1000 * covered.size) / denominator) / 10 : null,
    chargers: nChargers,
    coveredUnfiltered: unfiltered.size,
  };
}

/**
 * Per-country coverage rows for the fleet table. A company counts for the
 * country of the charger that reaches it, so the rows answer "how covered is
 * each country", not "how many Belgian companies does a German charger serve".
 */
export function coverageByCountry(
  meta: CoverageMeta,
  pairs: Uint32Array,
  filter: CoverageFilter,
): CoverageCountryRow[] {
  const { ok } = matchingChargers(meta, filter);
  const countrySet = filter.countries ? new Set(filter.countries) : null;
  const mask = maskFor(meta.companyBits);

  const coveredBy = new Map<string, Set<number>>();
  const chargersBy = new Map<string, number>();

  for (let i = 0; i < meta.chargers.length; i++) {
    if (!ok[i]) continue;
    const cc = meta.chargers[i][0];
    chargersBy.set(cc, (chargersBy.get(cc) ?? 0) + 1);
  }

  for (let i = 0; i < pairs.length; i++) {
    const v = pairs[i];
    const ci = v >>> meta.companyBits;
    if (!ok[ci]) continue;
    const cc = meta.chargers[ci][0];
    let set = coveredBy.get(cc);
    if (!set) {
      set = new Set<number>();
      coveredBy.set(cc, set);
    }
    set.add(v & mask);
  }

  const rows: CoverageCountryRow[] = [];
  for (const [country, n] of chargersBy) {
    if (countrySet && !countrySet.has(country)) continue;
    const denominator = meta.companiesByCountry[country] ?? 0;
    const coveredCount = coveredBy.get(country)?.size ?? 0;
    rows.push({
      country,
      covered: coveredCount,
      denominator,
      pct: denominator > 0 ? Math.round((1000 * coveredCount) / denominator) / 10 : null,
      chargers: n,
    });
  }
  rows.sort((a, b) => b.chargers - a.chargers);
  return rows;
}


/**
 * The charger indexes that pass the filter. The map uses this to draw the
 * radius circle around exactly the chargers the coverage number counts.
 */
export function matchingChargerIndexes(
  meta: CoverageMeta,
  filter: CoverageFilter,
): number[] {
  const { ok } = matchingChargers(meta, filter);
  const out: number[] = [];
  for (let i = 0; i < ok.length; i++) if (ok[i]) out.push(i);
  return out;
}
