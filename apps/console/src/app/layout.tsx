import type { Metadata } from "next";
import { Albert_Sans, Bricolage_Grotesque, Spline_Sans_Mono } from "next/font/google";
import "./globals.css";
import { Shell } from "@/components/shell";

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

export const metadata: Metadata = {
  title: "Mnemos — accountable memory for agents",
  description:
    "Memory tiers, the trust lattice, physical residency, the living audit chain, and erasure with proof.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${bricolage.variable} ${albert.variable} ${spline.variable}`}>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
