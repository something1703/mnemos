import type { Metadata } from "next";
import { Albert_Sans, Bricolage_Grotesque, Spline_Sans_Mono } from "next/font/google";
import "./globals.css";

/* The three families BRAND.md locks. Loaded through next/font so they are
 * self-hosted and subset at build time — no layout shift, no third-party
 * request on a page that shows governance evidence. */
const bricolage = Bricolage_Grotesque({
  subsets: ["latin"],
  variable: "--font-bricolage",
  display: "swap",
});
const albert = Albert_Sans({
  subsets: ["latin"],
  variable: "--font-albert",
  display: "swap",
});
const spline = Spline_Sans_Mono({
  subsets: ["latin"],
  variable: "--font-spline",
  display: "swap",
});

const TITLE = "Mnemos — accountable memory for agents";
const DESCRIPTION =
  "Memory tiers, the trust lattice, physical residency, the living audit chain, and erasure with proof.";

export const metadata: Metadata = {
  title: { default: TITLE, template: "%s · Mnemos" },
  description: DESCRIPTION,
  openGraph: { title: TITLE, description: DESCRIPTION, type: "website" },
  twitter: { card: "summary_large_image", title: TITLE, description: DESCRIPTION },
};

/**
 * No `<Shell>` here — that is `/console`'s own layout now. This root only
 * carries what every page needs regardless of whether it is the marketing
 * site or the dashboard: fonts and the brand stylesheet.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${bricolage.variable} ${albert.variable} ${spline.variable}`}>
      <body>
        {/* motion's `initial` state (opacity:0, etc.) serializes into the
            SSR HTML and only reverses once React hydrates. On a slow
            connection or with JS disabled entirely, every Reveal-wrapped
            section and the hero logo would otherwise stay invisible
            forever — worse than no animation at all. */}
        <noscript>
          <style>{`[style*="opacity:0"], [style*="opacity: 0"] { opacity: 1 !important; }`}</style>
        </noscript>
        {children}
      </body>
    </html>
  );
}
