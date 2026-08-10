import { apiGet } from "@/lib/api/server";

/**
 * Proxy the API's self-contained, offline-verifying HTML export.
 *
 * A route handler rather than a direct link to the API: the export is
 * authenticated, and linking the browser straight at it would mean either
 * shipping a key to the client or making the endpoint public. Neither is
 * acceptable for a document that contains recalled memory.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ actionId: string }> },
) {
  const { actionId } = await params;
  try {
    const html = await apiGet<string>(
      `/v1/deposition/${encodeURIComponent(actionId)}/export.html`,
      { revalidate: false, raw: true },
    );
    return new Response(html, {
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Disposition": `attachment; filename="deposition-${actionId}.html"`,
      },
    });
  } catch (cause) {
    return new Response(cause instanceof Error ? cause.message : "export failed", { status: 502 });
  }
}
