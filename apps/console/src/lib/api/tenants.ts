import "server-only";
import { cookies } from "next/headers";

/**
 * Which tenant the console is currently reading, and the key that proves it.
 *
 * The API derives the tenant from the API key and offers no way to override it
 * — there is no `/ledger/{tenant}` path parameter, on purpose (see
 * `services/api/src/mnemos_api/rest.py`'s module docstring: taking a tenant
 * from the URL invites the bug where a caller passes someone else's and the
 * handler forgets to check). So "switching tenants" in the console is not a
 * filter applied to a shared dataset. It is presenting a different credential,
 * and the isolation a judge sees when the numbers change is the real thing —
 * RLS and key-to-tenant resolution, not a WHERE clause this app could get
 * wrong.
 *
 * Keys live only in server environment variables and are read only here.
 */

export interface Tenant {
  slug: string;
  label: string;
  blurb: string;
}

/** The three demo verticals, in the order the story is told. */
export const TENANTS: readonly Tenant[] = [
  { slug: "clinic", label: "Clinic", blurb: "Patient intake, allergies, legal hold" },
  { slug: "ops", label: "Ops", blurb: "Cluster health, the Custodian, one poisoned source" },
  { slug: "finance", label: "Finance", blurb: "Credit decisions and their depositions" },
] as const;

export const TENANT_COOKIE = "mnemos_tenant";

const KEYS: Record<string, string | undefined> = {
  clinic: process.env.MNEMOS_API_KEY_READ_CLINIC,
  ops: process.env.MNEMOS_API_KEY_READ_OPS,
  finance: process.env.MNEMOS_API_KEY_READ_FINANCE,
};

/** The tenant to fall back to: the first one that actually has a key. */
function defaultSlug(): string {
  return TENANTS.find((t) => KEYS[t.slug])?.slug ?? TENANTS[0].slug;
}

export function tenantsWithKeys(): readonly Tenant[] {
  return TENANTS.filter((t) => Boolean(KEYS[t.slug]));
}

export async function activeTenant(): Promise<Tenant> {
  const requested = (await cookies()).get(TENANT_COOKIE)?.value;
  // Only ever trust the cookie as a lookup into a list we control. A cookie is
  // client-writable, so treating it as anything other than an index would let
  // a visitor name their own tenant — and the key, not the name, is what the
  // API would honour anyway.
  const match = TENANTS.find((t) => t.slug === requested && KEYS[t.slug]);
  return match ?? TENANTS.find((t) => t.slug === defaultSlug())!;
}

export async function activeTenantKey(): Promise<string> {
  const tenant = await activeTenant();
  return KEYS[tenant.slug] ?? "";
}
