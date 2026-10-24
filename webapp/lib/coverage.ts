/**
 * Live coverage calculation for DepotChargeDex.
 *
 * The question the site has to answer:
 *
 *   "With the filters I have set, what share of the transport companies
 *    within range of a charging station is covered?"
 *
 * That depends on three things at once:
 *
 *   - the radius (500 m, 1 km, 2 km, 5 km)
 *   - the filter (country, access class, power band, HGV only)
 *   - the company set (the denominator)
 *
 * The pair table ships precomputed from the server: for every
 * charger-company pair within 5 km, the charger index, the company index and
 * the distance in metres. From that, the covered share for any filter is a
 * DISTINCT count over the pairs that pass the filter.
 *
 * The count must be a distinct count over COMPANIES, not over pairs. A
 * company with four chargers within range is still one company. The server
 * runs the same maths (see scripts/export_coverage_pairs.py) and prints the
 * default case, so the two can be compared.
 */

export interface CoveragePairs {
  radii: number[];
  powerBands: { lo: number; hi: number | null }[];
  accessClasses: string[];
  /** Per charger: [country, accessClass, powerBandIndex, hgvFlag, tier] */
  chargers: [string, string, number, number, string][];
  /** Per pair: [chargerIndex, companyIndex, distanceMetres] */
  pairs: [number, number, number][];
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
}

export interface CoverageResult {
  /** distinct companies within range of at least one matching charger */
  covered: number;
  /** the companies that could be covered */
  denominator: number;
  /** share in percent, or null when there is no denominator */
  pct: number | null;
  /** chargers that pass the filter */
  chargers: number;
  /** companies within range of ANY charger, ignoring the filter */
  coveredUnfiltered: number;
}

/** The power band a charger with this kW falls into. Mirrors the server. */
export function bandFor(powerKw: number | null, bands: { lo: number; hi: number | null }[]): number {
  if (powerKw === null || powerKw === undefined) return 0;
  for (let i = 0; i < bands.length; i++) {
    const b = bands[i];
    if (powerKw >= b.lo && (b.hi === null || powerKw < b.hi)) return i;
  }
  return bands.length - 1;
}

/**
 * Count the coverage for a filter.
 *
 * This runs on every filter change, so it is written as one pass over the
 * pair list with the filter tested inline. 47,616 pairs is small enough that
 * this takes a few milliseconds; no index is needed.
 */
export function computeCoverage(
  data: CoveragePairs,
  filter: CoverageFilter,
): CoverageResult {
  const countrySet = filter.countries ? new Set(filter.countries) : null;
  const accessSet = filter.access ? new Set(filter.access) : null;
  const bandSet = filter.bands ? new Set(filter.bands) : null;

  // The distinct companies covered, and the same count without the filter,
  // so the UI can show how much the filter costs.
  const coveredSet = new Set<number>();
  const unfilteredSet = new Set<number>();
  const matchingChargers = new Set<number>();

  const { chargers, pairs } = data;
  for (let i = 0; i < pairs.length; i++) {
    const p = pairs[i];
    if (p[2] > filter.radiusM) continue;
    const c = chargers[p[0]];
    if (!c) continue;

    const country = c[0];
    const accessClass = c[1];
    const band = c[2];
    const hgv = c[3] === 1;

    unfilteredSet.add(p[1]);

    if (countrySet && !countrySet.has(country)) continue;
    if (accessSet && !accessSet.has(accessClass)) continue;
    if (bandSet && !bandSet.has(band)) continue;
    if (filter.hgvOnly && !hgv) continue;

    matchingChargers.add(p[0]);
    coveredSet.add(p[1]);
  }

  let denominator = 0;
  if (countrySet) {
    for (const cc of countrySet) denominator += data.companiesByCountry[cc] ?? 0;
  } else {
    denominator = data.companiesTotal;
  }

  return {
    covered: coveredSet.size,
    denominator,
    pct: denominator > 0 ? Math.round((1000 * coveredSet.size) / denominator) / 10 : null,
    chargers: matchingChargers.size,
    coveredUnfiltered: unfilteredSet.size,
  };
}

/**
 * The per-country coverage rows for the fleet table.
 *
 * The same pair table, split by the country of the charger. A company counts
 * for a country when it is covered by a charger in that country, so the rows
 * add up to the total.
 */
export function coverageByCountry(
  data: CoveragePairs,
  filter: CoverageFilter,
): { country: string; covered: number; denominator: number; pct: number | null; chargers: number }[] {
  const accessSet = filter.access ? new Set(filter.access) : null;
  const bandSet = filter.bands ? new Set(filter.bands) : null;
  const countryFilter = filter.countries ? new Set(filter.countries) : null;

  const coveredByCountry = new Map<string, Set<number>>();
  const chargersByCountry = new Map<string, Set<number>>();

  for (const p of data.pairs) {
    if (p[2] > filter.radiusM) continue;
    const c = data.chargers[p[0]];
    if (!c) continue;
    const country = c[0];
    if (countryFilter && !countryFilter.has(country)) continue;
    if (accessSet && !accessSet.has(c[1])) continue;
    if (bandSet && !bandSet.has(c[2])) continue;
    if (filter.hgvOnly && c[3] !== 1) continue;

    let set = coveredByCountry.get(country);
    if (!set) {
      set = new Set<number>();
      coveredByCountry.set(country, set);
    }
    set.add(p[1]);

    let cset = chargersByCountry.get(country);
    if (!cset) {
      cset = new Set<number>();
      chargersByCountry.set(country, cset);
    }
    cset.add(p[0]);
  }

  const rows: { country: string; covered: number; denominator: number; pct: number | null; chargers: number }[] = [];
  for (const [country, set] of coveredByCountry) {
    const denominator = data.companiesByCountry[country] ?? 0;
    rows.push({
      country,
      covered: set.size,
      denominator,
      pct: denominator > 0 ? Math.round((1000 * set.size) / denominator) / 10 : null,
      chargers: chargersByCountry.get(country)?.size ?? 0,
    });
  }
  rows.sort((a, b) => b.covered - a.covered);
  return rows;
}
