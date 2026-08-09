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

const BASE = (process.env.MNEMOS_API_URL ?? "").replace(/\/$/, "");
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
  { key = READ_KEY, revalidate = 4 }: { key?: string; revalidate?: number | false } = {},
): Promise<T> {
  if (!BASE) {
    throw new ApiError("MNEMOS_API_URL is not set — copy .env.example and fill it in", 500);
  }
  const response = await fetch(`${BASE}${path}`, {
    headers: key ? { Authorization: `Bearer ${key}` } : {},
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
  return (await response.json()) as T;
}
