import { callTool } from "@/lib/api/mcp";

/**
 * Blast radius preview, on the console's own read credential.
 *
 * Deliberately NOT behind the admin gate: `blast_radius` changes nothing (its
 * own tool description says PREVIEW ONLY), and seeing what a source
 * contaminated should never require holding the credential that could destroy
 * it. Verified against the live API — a write-scoped key is accepted here and
 * refused by `forget`.
 */
export async function POST(request: Request) {
  let body: { source_event_ids?: string[] };
  try {
    body = await request.json();
  } catch {
    return Response.json({ ok: false, error: "malformed request" }, { status: 400 });
  }

  const ids = (body.source_event_ids ?? []).filter((id) => typeof id === "string" && id.trim());
  if (ids.length === 0) {
    return Response.json({ ok: false, error: "at least one event id is required" });
  }

  try {
    const result = await callTool("blast_radius", { source_event_ids: ids });
    return Response.json({ ok: true, result });
  } catch (cause) {
    return Response.json({
      ok: false,
      error: cause instanceof Error ? cause.message : "blast radius failed",
    });
  }
}
