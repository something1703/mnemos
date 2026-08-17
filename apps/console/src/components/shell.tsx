import Link from "next/link";
import {
  Activity,
  ArrowLeft,
  Clock,
  FileSearch,
  Globe2,
  Link2,
  Radar,
  Search,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { TraceFooter } from "@/components/trace-footer";
import { TenantSwitcher } from "@/components/tenant-switcher";
import { NavLink } from "@/components/nav-link";
import { Logo } from "@/components/ui/logo";
import { activeTenant, tenantsWithKeys } from "@/lib/api/tenants";

/**
 * The Shell: nav rail plus the live memory-trace footer.
 *
 * The nav order is the story order a judge should walk — what is here, what it
 * knows, how it changed, where it lives, whether it can be proven, who is
 * watching it, what one source contaminated, and finally the two irreversible
 * actions. Destruction sits last and visually apart because it is not a peer
 * of "Overview".
 */
const NAV = [
  { href: "/console", label: "Overview", icon: Activity },
  { href: "/console/explorer", label: "Memory Explorer", icon: Search },
  { href: "/console/time-machine", label: "Time Machine", icon: Clock },
  { href: "/console/residency", label: "Residency", icon: Globe2 },
  { href: "/console/ledger", label: "Ledger", icon: Link2 },
  { href: "/console/custodian", label: "Custodian", icon: Radar },
  { href: "/console/deposition", label: "Deposition", icon: FileSearch },
] as const;

const DESTRUCTIVE_NAV = [
  { href: "/console/blast-radius", label: "Blast Radius", icon: ShieldAlert },
  { href: "/console/forget", label: "Forget", icon: Trash2 },
] as const;

export async function Shell({ children }: { children: React.ReactNode }) {
  const [tenants, active] = await Promise.all([tenantsWithKeys(), activeTenant()]);
  return (
    <div className="flex min-h-screen flex-col">
      <div className="flex flex-1">
        <nav
          aria-label="Primary"
          className="hidden w-56 shrink-0 flex-col gap-1 border-r border-hairline bg-veil/40 px-3 py-4 md:flex"
        >
          <Link href="/console" className="mb-1 flex items-center gap-2 px-2">
            <Logo size={22} className="text-synapse" />
            <span className="font-display text-lg tracking-[-0.02em] text-parchment">Mnemos</span>
          </Link>
          <Link
            href="/"
            className="mb-4 flex items-center gap-1 px-2 text-xs text-moonstone transition-colors hover:text-parchment"
          >
            {/* Not a domain — PRODUCT.md is explicit that there is no public
                URL yet. This links back to "/" within the same deployment,
                which is real. */}
            <ArrowLeft className="size-3" aria-hidden /> Back to the site
          </Link>

          <TenantSwitcher tenants={[...tenants]} active={active} />

          {NAV.map(({ href, label, icon: Icon }) => (
            <NavLink key={href} href={href} label={label} icon={<Icon className="size-4" aria-hidden />} />
          ))}

          <div className="my-3 border-t border-hairline" />
          <p className="px-2 pb-1 text-xs font-medium tracking-wide text-moonstone uppercase">
            Irreversible
          </p>
          {DESTRUCTIVE_NAV.map(({ href, label, icon: Icon }) => (
            <NavLink key={href} href={href} label={label} icon={<Icon className="size-4" aria-hidden />} />
          ))}
        </nav>

        <main className="min-w-0 flex-1 px-5 py-6 md:px-8">{children}</main>
      </div>

      <TraceFooter />
    </div>
  );
}
