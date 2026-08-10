import "server-only";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import type { TrustState } from "@/lib/brand";

/**
 * The MCP half of the BFF.
 *
 * The REST surface covers browsing (`/v1/facts`, `/v1/ledger`), but the two
 * things this console exists to show off — live hybrid recall and
 * `recall_as_of` — are MCP tools, because they are what an *agent* actually
 * calls. Driving the same tools the agent uses keeps the console honest: it
 * cannot show a capability the agent does not have.
 *
 * Always server-side. A browser holding a Mnemos key is exactly what Phase
 * 08.3 forbids, and the admin-scoped calls below take their key per-request
 * rather than reading one from the environment at all.
 */

const BASE = (process.env.MNEMOS_API_URL ?? "").replace(/\/$/, "");
const READ_KEY = process.env.MNEMOS_API_KEY_READ ?? "";

export interface RecalledFact {
  fact_id: string;
  subject_key: string;
  fact_kind: string;
  text: string | null;
  trust: TrustState;
  home_region: string;
  confidence: number;
  corroboration_count: number;
  score: number;
  score_breakdown: {
    similarity: number;
    strength: number;
    confidence: number;
    trust_weight: number;
  };
  provenance: { event_id: string; weight: number }[];
}

export interface RecallResult {
  facts: RecalledFact[];
  withheld?: { unverified?: number; quarantined?: number; residency?: number } | null;
  recall_id?: string | null;
  [key: string]: unknown;
}

/**
 * One tool call, one connection.
 *
 * Deliberately not a cached long-lived client: this runs in serverless
 * request handlers where a module-scope connection outlives the request that
 * opened it and gets reused across concurrent invocations, and an MCP session
 * is stateful. Connection setup is one HTTP round trip against a Lambda that
 * is already warm — cheap enough that correctness wins.
 */
export async function callTool<T = unknown>(
  name: string,
  args: Record<string, unknown>,
  { key = READ_KEY }: { key?: string } = {},
): Promise<T> {
  if (!BASE) throw new Error("MNEMOS_API_URL is not set");
  if (!key) throw new Error("No API key available for this call");

  const client = new Client({ name: "mnemos-console", version: "0.1.0" });
  const transport = new StreamableHTTPClientTransport(new URL(`${BASE}/mcp`), {
    requestInit: { headers: { Authorization: `Bearer ${key}` } },
  });

  try {
    await client.connect(transport);
    const result = await client.callTool({ name, arguments: args });

    if (result.isError) {
      const text = Array.isArray(result.content)
        ? result.content
            .map((block) => (block as { text?: string }).text ?? "")
            .join("\n")
            .trim()
        : "";
      throw new Error(text || `${name} failed`);
    }

    if (result.structuredContent) return result.structuredContent as T;

    const first = Array.isArray(result.content)
      ? (result.content[0] as { text?: string } | undefined)
      : undefined;
    return (first?.text ? JSON.parse(first.text) : null) as T;
  } finally {
    await client.close().catch(() => {});
  }
}

export const recall = (
  query: string,
  options: { k?: number; includeUnverified?: boolean; subjectKey?: string } = {},
) =>
  callTool<RecallResult>("recall", {
    query,
    k: options.k ?? 10,
    include_unverified: options.includeUnverified ?? false,
    ...(options.subjectKey ? { subject_key: options.subjectKey } : {}),
  });

export const recallAsOf = (query: string, asOf: string, options: { k?: number } = {}) =>
  callTool<RecallResult>("recall_as_of", { query, as_of: asOf, k: options.k ?? 10 });

export const whereIs = (subjectKey: string) =>
  callTool<Record<string, unknown>>("where_is", { subject_key: subjectKey });
