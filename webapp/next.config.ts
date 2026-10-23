import type { NextConfig } from "next";

// Only the OSM tile server is needed now — the CARTO basemap was dropped
// because it started requiring an API key (it served "API KEY REQUIRED"
// placeholder tiles without one).
const MAP_TILE_HOSTS = "https://tile.openstreetmap.org https://*.tile.openstreetmap.org";

// Two deployments build from this same source:
//   1. sportlaadpalen.laserraptorai.duckdns.org  — `next start`, no env vars.
//   2. zeroemission.duckdns.org/dashboards/sportlaadpalen/ — static export
//      (NEXT_STATIC_EXPORT=1) served under a sub-path (NEXT_PUBLIC_BASE_PATH).
// Both env vars are unset for deployment 1, so its behaviour is unchanged.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
const staticExport = process.env.NEXT_STATIC_EXPORT === "1";

const nextConfig: NextConfig = {
  compress: true,
  ...(basePath ? { basePath } : {}),
  ...(staticExport ? { output: "export" as const, trailingSlash: true } : {}),
  // headers() is a server feature; `output: "export"` cannot apply it. The
  // portal deployment serves the export through nginx, which sets its own
  // security headers, so gating it out here is safe.
  ...(staticExport ? {} : { async headers() {
    const securityHeaders = [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    ];
    const csp = [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
      "style-src 'self' 'unsafe-inline'",
      `img-src 'self' data: blob: ${MAP_TILE_HOSTS}`,
      "font-src 'self' data:",
      `connect-src 'self' ${MAP_TILE_HOSTS}`,
      "worker-src 'self' blob:",
      "child-src 'self' blob:",
    ].join("; ");

    return [
      {
        source: "/(.*)",
        headers: [
          ...securityHeaders,
          { key: "Content-Security-Policy", value: csp },
        ],
      },
      {
        source: "/data/:path*.geojson",
        headers: [
          // The data is regenerated on every deploy, so it must be revalidated
          // rather than served stale from a max-age window (an hour of stale
          // summary.json made the radius selector look like it ignored clicks).
          { key: "Cache-Control", value: "public, max-age=0, must-revalidate" },
          { key: "Content-Type", value: "application/geo+json" },
        ],
      },
      {
        source: "/data/:path*.json",
        headers: [
          { key: "Cache-Control", value: "public, max-age=0, must-revalidate" },
        ],
      },
    ];
  } }),
};

export default nextConfig;