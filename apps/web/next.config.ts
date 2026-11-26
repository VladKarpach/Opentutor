import type { NextConfig } from "next";
import path from "path";

// Two different addresses, and conflating them breaks Docker.
//
// NEXT_PUBLIC_API_URL is what the *browser* uses, so it must be reachable from
// the host: http://localhost:8010/api.
//
// API_INTERNAL_URL is what the *server-side* rewrite proxy uses. Inside the web
// container "localhost" is the web container itself, so the browser URL points
// the proxy back at itself and it fails with ECONNREFUSED. In Docker the API
// is reachable by service name on the compose network.
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000/api";
const API_INTERNAL_URL = process.env.API_INTERNAL_URL || API_URL;

const PYODIDE_ORIGIN = "https://cdn.jsdelivr.net";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-XSS-Protection", value: "1; mode=block" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      // Pyodide is loaded from a CDN on first use. Without the origin here the
      // browser refuses the dynamic import and the code runner silently degrades
      // to "check without running". `wasm-unsafe-eval` is the modern spelling of
      // the permission WebAssembly needs; `unsafe-eval` stays for older engines.
      `script-src 'self' 'unsafe-eval' 'unsafe-inline' 'wasm-unsafe-eval' ${PYODIDE_ORIGIN}`,
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      `connect-src 'self' ${API_URL.replace(/\/api$/, "")} ${PYODIDE_ORIGIN}${process.env.NODE_ENV === "development" ? " http://localhost:* ws://localhost:*" : ""}`,
      "worker-src 'self' blob:",
      "frame-ancestors 'none'",
    ].join("; "),
  },
];

const nextConfig: NextConfig = {
  outputFileTracingRoot: path.join(__dirname, "..", ".."),
  turbopack: {
    root: path.join(__dirname, "..", ".."),
  },
  // Prevent Next.js from issuing 308 redirects for trailing slashes on /api/* paths.
  // Without this, POST /api/courses/ gets a 308 before the rewrite runs, and the
  // redirected request may expose the backend origin, which CSP connect-src blocks.
  skipTrailingSlashRedirect: true,
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: securityHeaders,
      },
    ];
  },
  async rewrites() {
    return {
      // beforeFiles ensures the API proxy runs before Next.js file matching
      // and trailing slash normalization, preventing 308 redirect races.
      beforeFiles: [
        {
          // FastAPI serves chat SSE on `/api/chat/` and responds with an
          // absolute 307 redirect if the trailing slash is lost. Handle chat
          // explicitly so the browser stays on the Next.js origin.
          source: "/api/chat",
          destination: `${API_INTERNAL_URL}/chat/`,
        },
        {
          source: "/api/chat/",
          destination: `${API_INTERNAL_URL}/chat/`,
        },
        {
          // Preserve a trailing slash. The `:path*` capture below drops it, so
          // `/api/courses/` reaches FastAPI as `/api/courses`, which Starlette
          // answers with an absolute 307 to the backend origin. The browser
          // then leaves the Next.js origin to follow it and the response is
          // blocked by `connect-src` CSP — surfacing as "Failed to fetch" on
          // every collection POST (`/api/courses/`, `/api/preferences/`,
          // `/api/goals/`).
          source: "/api/:path*/",
          destination: `${API_INTERNAL_URL}/:path*/`,
        },
        {
          source: "/api/:path*",
          destination: `${API_INTERNAL_URL}/:path*`,
        },
      ],
      afterFiles: [],
      fallback: [],
    };
  },
};

export default nextConfig;
