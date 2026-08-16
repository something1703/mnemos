import Link from "next/link";
import { Logo } from "@/components/ui/logo";

export function SiteFooter() {
  return (
    <footer className="border-t border-hairline">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-5 py-10 md:flex-row md:items-start md:justify-between md:px-8">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Logo size={18} className="text-dim" />
            <span className="font-display text-sm text-moonstone">Mnemos</span>
          </div>
          <p className="max-w-sm text-xs text-dim">
            Built on{" "}
            <a
              href="https://www.cockroachlabs.com/"
              className="underline decoration-hairline underline-offset-2 hover:text-moonstone"
            >
              CockroachDB
            </a>{" "}
            and AWS for the CockroachDB × AWS &ldquo;Build with Agentic Memory&rdquo; hackathon.
          </p>
        </div>

        <div className="flex flex-wrap gap-x-10 gap-y-4 text-sm">
          <FooterGroup
            title="Product"
            links={[
              { href: "/how-it-works", label: "How it works" },
              { href: "/console", label: "Console" },
            ]}
          />
          <FooterGroup
            title="Console"
            links={[
              { href: "/console/explorer", label: "Memory Explorer" },
              { href: "/console/ledger", label: "Ledger" },
              { href: "/console/custodian", label: "Custodian" },
            ]}
          />
        </div>
      </div>
    </footer>
  );
}

function FooterGroup({
  title,
  links,
}: {
  title: string;
  links: { href: string; label: string }[];
}) {
  return (
    <div className="flex flex-col gap-2">
      <span className="text-[10px] font-medium tracking-wide text-dim uppercase">{title}</span>
      {links.map((l) => (
        <Link
          key={l.href}
          href={l.href}
          className="text-moonstone transition-colors hover:text-parchment"
        >
          {l.label}
        </Link>
      ))}
    </div>
  );
}
