import Link from "next/link";
import { Logo } from "@/components/ui/logo";
import { Button } from "@/components/ui/primitives";

const LINKS = [
  { href: "/how-it-works", label: "How it works" },
  { href: "/console", label: "Console" },
] as const;

export function SiteHeader() {
  return (
    <header className="border-b border-hairline">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5 md:px-8">
        <Link href="/" className="flex items-center gap-2">
          <Logo size={26} className="text-synapse" />
          <span className="font-display text-lg tracking-[-0.02em] text-parchment">Mnemos</span>
        </Link>

        <nav aria-label="Primary" className="hidden items-center gap-6 md:flex">
          {LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="text-sm text-moonstone transition-colors hover:text-parchment"
            >
              {link.label}
            </Link>
          ))}
        </nav>

        <Button asChild variant="primary" size="sm">
          <Link href="/console">Open the console</Link>
        </Button>
      </div>
    </header>
  );
}
