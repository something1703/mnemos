import type { NextConfig } from "next";

/**
 * Security headers, set here rather than in `vercel.json` so they apply
 * identically under `next dev`, `next start`, and on Vercel — a header that
 * only exists in production is a header nobody tests.
 *
 * The CSP is deliberately strict about where things may be *sent*:
 * `connect-src 'self'` means the browser cannot reach the Mnemos API (or
 * anywhere else) directly. That is not decoration — the whole credential
 * story of this console depends on every API call going through the server
 * (`src/lib/api/server.ts`), and this makes the browser structurally unable
 * to do otherwise, even if a future component tried.
 *
 * `'unsafe-inline'` for styles is Tailwind + Next's inlined critical CSS;
 * scripts get `'unsafe-inline'` too because Next's bootstrap uses inline
 * scripts and nonce-ing them app-wide would mean giving up static
 * optimisation for no real gain here — an XSS in this app would have no
 * credential to steal, since none reaches the browser.
 */
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const nextConfig: NextConfig = {
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: CSP },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
