import type { FeatureCollection, Point } from "geojson";

export type Tier = "A" | "B" | "C";

/** Who may use the site: open to all, depot/shared, private-only, or unproven. */
export type AccessClass = "public" | "semi_public" | "private" | "unknown";

export interface DepotProperties {
  id: string;
  operator: string;
  address: string;
  city: string;
  postcode: string;
  province: string;
  country?: string;
  tier: Tier;
  score: number;
  /** null when no source mentioned a power figure — never guessed. */
  powerKw: number | null;
  nPoints: number | null;
  hgv: boolean;
  /** Who may use the site; the exporter defaults a missing verdict to "unknown". */
  accessClass: AccessClass;
  channels: string[];
  evidenceCount: number;
  evidence: string[];
  /** address | postcode | city | signal — how precise the map point is. */
  precision: string;
}

export type Depots = FeatureCollection<Point, DepotProperties>;

export interface Summary {
  generatedAt: string;
  sites: {
    total: number;
    scored: number;
    withoutCoords: number;
    byTier: Record<Tier, number>;
    hgv: number;
  };
  power: { totalKw: number; sitesWithKw: number };
  byProvince: Record<string, number>;
  byChannel: Record<string, number>;
  precision: Record<string, number>;
  sources: Record<string, string>;
}

export const TIER_COLOR: Record<Tier, string> = {
  A: "#b45309", // amber-700 — evidence names the site and its address
  B: "#d97706", // amber-600 — strong evidence, city-level
  C: "#f59e0b", // amber-500 — lead
};

export const TIER_LABEL: Record<Tier, string> = {
  A: "A — bewezen locatie",
  B: "B — waarschijnlijk",
  C: "C — lead",
};

export const TIER_HINT: Record<Tier, string> = {
  A: "Operator, vergunning of publicatie noemt bedrijf én volledig adres.",
  B: "Bewijs noemt het bedrijf en de plaats, adres ontbreekt.",
  C: "Signaal zonder locatiebevestiging — handmatig verifiëren.",
};

export const ACCESS_LABEL: Record<AccessClass, string> = {
  public: "Publiek",
  semi_public: "Semi-publiek",
  private: "Privaat",
  unknown: "Onbekend",
};

export const ACCESS_HINT: Record<AccessClass, string> = {
  public: "Vrij toegankelijk voor derden.",
  semi_public: "Depot of collectief: gebruik via afspraak of vergunning.",
  private: "Alleen voor het bedrijf zelf.",
  unknown: "Toegang niet vastgesteld in het bewijs.",
};

export const CHANNEL_LABEL: Record<string, string> = {
  news: "vakpers",
  permit: "vergunning",
  osm: "OSM",
};
