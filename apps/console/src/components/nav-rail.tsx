"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

export interface NavItem {
  href: string;
  label: string;
  /** An already-rendered icon element, not a component reference — Shell is
   * a server component, and a bare function/component type is not
   * serializable across that server→client boundary. */
  icon: React.ReactNode;
}

/**
 * One measured highlight shared across a group of nav links, sliding to
 * whichever item the pointer or keyboard focus lands on and flowing back to
 * the active route the moment nothing is hovered.
 *
 * This is deliberately layered, not merged, with the 2px synapse edge bar
 * DESIGN.md's Navigation spec calls for: the edge bar is the one
 * ground-truth "you are here" signal and never moves or changes color; this
 * pill is purely the transient "your pointer is over this one right now"
 * layer, tinted synapse only when it happens to be sitting on the active
 * item and a neutral veil-hi everywhere else — so hovering a page you are
 * NOT on never reads as if you were on it.
 */
export function NavRail({ items }: { items: NavItem[] }) {
  const pathname = usePathname();
  const [hovered, setHovered] = React.useState<string | null>(null);
  const [box, setBox] = React.useState<{ top: number; height: number } | null>(null);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const itemRefs = React.useRef<Record<string, HTMLAnchorElement | null>>({});

  const activeHref = React.useMemo(
    () =>
      items.find((item) =>
        item.href === "/console" ? pathname === item.href : pathname.startsWith(item.href),
      )?.href,
    [items, pathname],
  );

  const focusHref = hovered ?? activeHref ?? null;

  React.useLayoutEffect(() => {
    const container = containerRef.current;
    const target = focusHref ? itemRefs.current[focusHref] : null;
    if (!container || !target) {
      setBox(null);
      return;
    }
    const containerRect = container.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    setBox({ top: targetRect.top - containerRect.top, height: targetRect.height });
  }, [focusHref]);

  const pillIsActive = focusHref !== null && focusHref === activeHref;

  return (
    <div ref={containerRef} onMouseLeave={() => setHovered(null)} className="relative flex flex-col gap-1">
      <span
        aria-hidden
        className={cn(
          "pointer-events-none absolute inset-x-0 rounded",
          pillIsActive ? "bg-synapse-fill" : "bg-veil-hi",
        )}
        style={{
          top: box?.top ?? 0,
          height: box?.height ?? 0,
          opacity: box ? 1 : 0,
          transition:
            "top 200ms cubic-bezier(0.23,1,0.32,1), height 200ms cubic-bezier(0.23,1,0.32,1), opacity 150ms ease, background-color 150ms ease",
        }}
      />
      {items.map((item) => {
        const active = item.href === activeHref;
        return (
          <Link
            key={item.href}
            href={item.href}
            ref={(el) => {
              itemRefs.current[item.href] = el;
            }}
            aria-current={active ? "page" : undefined}
            onMouseEnter={() => setHovered(item.href)}
            onFocus={() => setHovered(item.href)}
            onBlur={() => setHovered(null)}
            className={cn(
              "group relative z-10 flex items-center gap-2.5 rounded px-2 py-1.5 text-sm transition-colors duration-[120ms] ease-brand",
              active ? "text-synapse" : "text-moonstone hover:text-parchment",
            )}
          >
            <span
              className={cn(
                "absolute top-1/2 -left-3 h-4 w-0.5 -translate-y-1/2 rounded-full bg-synapse transition-opacity duration-[120ms]",
                active ? "opacity-100" : "opacity-0",
              )}
              aria-hidden
            />
            {item.icon}
            {item.label}
          </Link>
        );
      })}
    </div>
  );
}
