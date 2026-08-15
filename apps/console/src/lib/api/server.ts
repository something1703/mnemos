import "server-only";

/**
 * The server side of the console's BFF.
 *
 * Every call to the Mnemos API happens here, never in the browser, for one
 * reason that Phase 08.3 states as a requirement: the read key must not be
 * shipped to the client, and the admin key must never be "held in local
 * storage". Keeping the fetch server-side means the browser holds no Mnemos
 * credential at all — the admin key is posted to a route handler for exactly
 * one operation and is gone the moment that request ends.
 */

import { activeTenantKey } from "./tenants";

const BASE = (process.env.MNEMOS_API_URL ?? "").replace(/\/$/, "");
/** Single-tenant fallback, for a deployment that sets only one read key. */
const READ_KEY = process.env.MNEMOS_API_KEY_READ ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function apiConfigured(): boolean {
  return Boolean(BASE);
}

/**
 * `revalidate` rather than `no-store` by default: the Overview polls every 15s
 * and the ledger every 5s (Phase 08.3), and a shared 4s cache stops N widgets
 * on one screen from becoming N round trips to Lambda.
 */
export async function apiGet<T>(
  path: string,
  {
    key,
    revalidate = 4,
    raw = false,
  }: { key?: string; revalidate?: number | false; raw?: boolean } = {},
): Promise<T> {
  if (!BASE) {
    throw new ApiError("MNEMOS_API_URL is not set — copy .env.example and fill it in", 500);
  }
  // Resolved per request from the tenant cookie, so switching tenants presents
  // a different credential rather than filtering a shared result. Callers may
  // still pass `key` explicitly — the admin flows do, with a key that exists
  // only for the duration of one request.
  const resolved = key ?? (await activeTenantKey()) ?? READ_KEY;
  const response = await fetch(`${BASE}${path}`, {
    headers: resolved ? { Authorization: `Bearer ${resolved}` } : {},
    next: revalidate === false ? undefined : { revalidate },
    cache: revalidate === false ? "no-store" : undefined,
  });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new ApiError(
      `${path} returned ${response.status}${body ? `: ${body.slice(0, 300)}` : ""}`,
      response.status,
    );
  }
  // `raw` is for the deposition export, which is HTML by design — parsing it
  // as JSON would throw on a response that is perfectly valid.
  if (raw) return (await response.text()) as T;
  return (await response.json()) as T;
}
