import { callTool } from "@/lib/api/mcp";

/**
 * The one place the console can reach an admin-scoped tool.
 *
 * The admin key arrives in the request body, is used for exactly this call,
 * and is never written anywhere — not to a cookie, not to local storage, not
 * to a log line (Phase 08.3: "admin key re-entry gated inside the Forget and
 * Revoke flows only, never held in local storage"). The browser holds it only
 * for as long as the form is open.
 *
 * `tool` is checked against an allowlist rather than passed through. The
 * caller already holds the key, so this is not a privilege boundary — it is
 * the same reasoning as mnemos_custodian.allowlist: an endpoint that forwards
 * an arbitrary tool name is a shape worth not having, because the next person
 * to add a tool gets it exposed here for free without deciding to.
 */
const ALLOWED = new Set(["forget", "revoke_source", "blast_radius", "set_legal_hold"]);

export async function POST(request: Request) {
  let body: { key?: string; tool?: string; args?: Record<string, unknown> };
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "malformed request" }, { status: 400 });
  }

  const { key, tool, args } = body;
  if (!key || typeof key !== "string") {
    return Response.json({ error: "An admin key is required for this operation." }, { status: 400 });
  }
  if (!tool || !ALLOWED.has(tool)) {
    return Response.json({ error: `tool ${tool ?? "(none)"} is not callable here` }, { status: 400 });
  }

  try {
    const result = await callTool(tool, args ?? {}, { key });
    return Response.json({ ok: true, result });
  } catch (cause) {
    // The message is surfaced verbatim: a refusal from the Warden carries the
    // reason that matters (a legal hold's matter reference, or DUAL CONTROL),
    // and paraphrasing it here would throw away the only useful part.
    return Response.json(
      { ok: false, error: cause instanceof Error ? cause.message : "the operation failed" },
      { status: 200 },
    );
  }
}
