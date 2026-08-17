"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A worked example that lives on one specific tenant's real data — the
 * Deposition and Blast Radius screens both need this, since neither has any
 * way to discover a valid id on its own (P0 in the design review: two of
 * three product pillars were dead ends for a visitor with no id to paste).
 *
 * Rather than a plain link that 404s when the visitor is on the wrong
 * tenant, this switches tenants first (the same POST the tenant switcher
 * itself uses) and only then navigates — so "try it" works regardless of
 * which tenant happened to be active, the same way a real operator would
 * actually reach this data: by holding that tenant's key.
 */
export function TryExampleLink({
  tenantSlug,
  href,
  children,
  className,
}: {
  tenantSlug: string;
  href: string;
  children: React.ReactNode;
  className?: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  return (
    <button
      type="button"
      disabled={pending}
      onClick={() => {
        startTransition(async () => {
          await fetch("/api/tenant", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ slug: tenantSlug }),
          });
          router.push(href);
          router.refresh();
        });
      }}
      className={cn(
        "inline-flex items-center gap-1.5 text-left text-synapse hover:underline disabled:opacity-60",
        className,
      )}
    >
      {pending ? "Switching to the " + tenantSlug + " tenant…" : children}
      {!pending ? <ArrowRight className="size-3" aria-hidden /> : null}
    </button>
  );
}
