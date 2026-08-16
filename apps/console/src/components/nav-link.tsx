"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

/**
 * A nav rail item that knows where it is. Split out from `Shell` (a server
 * component, so it can fetch the active tenant without a client round trip)
 * because `usePathname` needs the client boundary — keeping it to one small
 * leaf rather than making the whole rail client-side.
 */
export function NavLink({
  href,
  label,
  icon,
}: {
  href: string;
  label: string;
  /** An already-rendered icon element, not a component reference — a bare
   * function/component type is not serializable across the server→client
   * boundary this component sits behind (`Shell` is a server component).
   * The caller renders `<Icon />` and hands the element down. */
  icon: React.ReactNode;
}) {
  const pathname = usePathname();
  const active = href === "/console" ? pathname === href : pathname.startsWith(href);

  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group relative flex items-center gap-2.5 rounded px-2 py-1.5 text-sm transition-colors duration-[120ms] ease-brand",
        active
          ? "bg-synapse-fill text-synapse"
          : "text-moonstone hover:bg-veil-hi hover:text-parchment",
      )}
    >
      <span
        className={cn(
          "absolute top-1/2 -left-3 h-4 w-0.5 -translate-y-1/2 rounded-full bg-synapse transition-opacity duration-[120ms]",
          active ? "opacity-100" : "opacity-0",
        )}
        aria-hidden
      />
      {icon}
      {label}
    </Link>
  );
}
