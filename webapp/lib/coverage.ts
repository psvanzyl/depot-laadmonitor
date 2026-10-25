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

/* ------------------------------------------------------------------ *
 * Long radii: charger-class masks instead of pairs.
 *
 * 50 km is 9.0 M pairs (34.5 MB) and 100 km is 29.7 M (113.4 MB), so the
 * pair file cannot carry the long end. But the question there is not which
 * charger reaches which company — it is whether ANY charger of this class
 * reaches it. So the index inverts: one record per company holding a bitmask
 * of the charger classes in range.
 *
 * A class is the full filter tuple (tier, access, band, source, hgv), so a
 * filter is also a bitmask and "covered" is one AND per company. The answer is
 * exact, for every filter, from one file per radius.
 * ------------------------------------------------------------------ */

export interface MaskData {
  radius: number;
  nClasses: number;
  countries: string[];
  /** nClasses records of 5 bytes: tier letter, access index, band, source, hgv */
  classes: Uint8Array;
  /** nRecords records of 4 words: countryIdx, mask0, mask1, mask2 */
  records: Uint32Array;
}

/** Header is 21 bytes; records start on a 4-byte boundary after the padding. */
export function parseMask(buf: ArrayBuffer): MaskData {
  const dv = new DataView(buf);
  const magic = String.fromCharCode(
    dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3),
  );
  if (magic !== "DCDM") throw new Error("coverage mask: bad magic");
  const radius = dv.getUint32(5, true);
  const nClasses = dv.getUint16(9, true);
  const nRecords = dv.getUint32(11, true);
  const nCountries = dv.getUint16(15, true);

  let off = 21;
  const classes = new Uint8Array(buf, off, nClasses * 5);
  off += nClasses * 5;
  const countries: string[] = [];
  for (let i = 0; i < nCountries; i++) {
    countries.push(
      String.fromCharCode(dv.getUint8(off)) + String.fromCharCode(dv.getUint8(off + 1)),
    );
    off += 2;
  }
  off += (4 - (off % 4)) % 4;
  const records = new Uint32Array(buf, off, nRecords * 4);
  return { radius, nClasses, countries, classes, records };
}

const ACCESS_ORDER = ["public", "semi_public", "private", "unknown"];

/** Which class bits the current filter selects. */
function selectedBits(mask: MaskData, filter: CoverageFilter): Uint32Array {
  const bits = new Uint32Array(3);
  const accessSet = filter.access ? new Set(filter.access) : null;
  const bandSet = filter.bands ? new Set(filter.bands) : null;
  const sourceSet =
    filter.sources === undefined || filter.sources === null
      ? null
      : new Set<number>(filter.sources);

  for (let i = 0; i < mask.nClasses; i++) {
    const o = i * 5;
    const access = ACCESS_ORDER[mask.classes[o + 1]];
    const band = mask.classes[o + 2];
    const source = mask.classes[o + 3];
    const hgv = mask.classes[o + 4];
    if (accessSet && !accessSet.has(access)) continue;
    if (bandSet && !bandSet.has(band)) continue;
    if (filter.hgvOnly && hgv !== 1) continue;
    if (sourceSet && !sourceSet.has(source)) continue;
    const w = i < 32 ? 0 : i < 62 ? 1 : 2;
    const shift = i < 32 ? i : i < 62 ? i - 32 : i - 62;
    bits[w] |= 1 << shift;
  }
  return bits;
}

function hits(rec: Uint32Array, i: number, sel: Uint32Array): boolean {
  const o = i * 4;
  return (
    (rec[o + 1] & sel[0]) !== 0 ||
    (rec[o + 2] & sel[1]) !== 0 ||
    (rec[o + 3] & sel[2]) !== 0
  );
}

export function computeCoverageMasks(
  meta: CoverageMeta,
  mask: MaskData,
  filter: CoverageFilter,
): CoverageResult {
  const sel = selectedBits(mask, filter);
  const countrySet = filter.countries ? new Set(filter.countries) : null;
  const { chargers } = matchingChargers(meta, filter);

  let covered = 0;
  let unfiltered = 0;
  for (let i = 0; i < mask.records.length / 4; i++) {
    const o = i * 4;
    const cc = mask.countries[mask.records[o]];
    if (countrySet && !countrySet.has(cc)) continue;
    const anyBit =
      mask.records[o + 1] !== 0 || mask.records[o + 2] !== 0 || mask.records[o + 3] !== 0;
    if (anyBit) unfiltered += 1;
    if (hits(mask.records, i, sel)) covered += 1;
  }

  let denominator = 0;
  if (countrySet) {
    for (const cc of countrySet) denominator += meta.companiesByCountry[cc] ?? 0;
  } else {
    denominator = meta.companiesTotal;
  }

  return {
    covered,
    denominator,
    pct: denominator > 0 ? Math.round((1000 * covered) / denominator) / 10 : null,
    chargers,
    coveredUnfiltered: unfiltered,
  };
}

export function coverageByCountryMasks(
  meta: CoverageMeta,
  mask: MaskData,
  filter: CoverageFilter,
): CoverageCountryRow[] {
  const sel = selectedBits(mask, filter);
  const countrySet = filter.countries ? new Set(filter.countries) : null;
  const { chargers } = matchingChargers(meta, filter);

  const covered = new Map<string, number>();
  for (let i = 0; i < mask.records.length / 4; i++) {
    const o = i * 4;
    const cc = mask.countries[mask.records[o]];
    if (countrySet && !countrySet.has(cc)) continue;
    if (!hits(mask.records, i, sel)) continue;
    covered.set(cc, (covered.get(cc) ?? 0) + 1);
  }

  return [...covered.entries()]
    .map(([country, n]) => {
      const den = meta.companiesByCountry[country] ?? 0;
      return {
        country,
        covered: n,
        denominator: den,
        pct: den > 0 ? Math.round((1000 * n) / den) / 10 : null,
        chargers,
      };
    })
    .sort((a, b) => b.covered - a.covered);
}
