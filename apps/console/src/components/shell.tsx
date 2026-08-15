import Link from "next/link";
import {
  Activity,
  Boxes,
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
  { href: "/", label: "Overview", icon: Activity },
  { href: "/explorer", label: "Memory Explorer", icon: Search },
  { href: "/time-machine", label: "Time Machine", icon: Clock },
  { href: "/residency", label: "Residency", icon: Globe2 },
  { href: "/ledger", label: "Ledger", icon: Link2 },
  { href: "/custodian", label: "Custodian", icon: Radar },
  { href: "/deposition", label: "Deposition", icon: FileSearch },
] as const;

const DESTRUCTIVE_NAV = [
  { href: "/blast-radius", label: "Blast Radius", icon: ShieldAlert },
  { href: "/forget", label: "Forget", icon: Trash2 },
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
          <Link href="/" className="mb-4 flex items-center gap-2 px-2">
            <Boxes className="size-5 text-synapse" aria-hidden />
            <span className="font-display text-lg tracking-[-0.02em] text-parchment">Mnemos</span>
          </Link>

          <TenantSwitcher tenants={[...tenants]} active={active} />

          {NAV.map(({ href, label, icon: Icon }) => (
            <NavLink key={href} href={href} label={label} Icon={Icon} />
          ))}

          <div className="my-3 border-t border-hairline" />
          <p className="px-2 pb-1 text-[10px] font-medium tracking-wide text-dim uppercase">
            Irreversible
          </p>
          {DESTRUCTIVE_NAV.map(({ href, label, icon: Icon }) => (
            <NavLink key={href} href={href} label={label} Icon={Icon} />
          ))}
        </nav>

        <main className="min-w-0 flex-1 px-5 py-6 md:px-8">{children}</main>
      </div>

      <TraceFooter />
    </div>
  );
}

function NavLink({
  href,
  label,
  Icon,
}: {
  href: string;
  label: string;
  Icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}) {
  return (
    <Link
      href={href}
      className="flex items-center gap-2.5 rounded px-2 py-1.5 text-sm text-moonstone transition-colors hover:bg-veil-hi hover:text-parchment"
    >
      <Icon className="size-4" aria-hidden />
      {label}
    </Link>
  );
}
