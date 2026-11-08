import type { NextConfig } from "next";

// The dashboards are client-rendered behind a session cookie; nothing is prerendered per user.
// Sub-path hosting (e.g. https://host/dashboard): NEXT_BASE_PATH at build time. Links, the router,
// static assets and the /api rewrite below all carry it; the API client reads NEXT_PUBLIC_BASE_PATH.
const basePath = (process.env.NEXT_BASE_PATH ?? "").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  output: "standalone",
  basePath: basePath || undefined,
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // Same-origin deployment: with API_INTERNAL_URL set (e.g. http://api:8000 in Compose) the web server
  // proxies /api/* to the API, the browser talks to one origin only and NEXT_PUBLIC_API_URL is "".
  async rewrites() {
    const target = process.env.API_INTERNAL_URL?.replace(/\/+$/, "");
    return target ? [{ source: "/api/:path*", destination: `${target}/api/:path*` }] : [];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
