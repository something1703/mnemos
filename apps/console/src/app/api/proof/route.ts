import { verifyLedger } from "@/lib/api/queries";
import { recall } from "@/lib/api/mcp";

/**
 * The two independent checks the proof screen makes after an erasure.
 *
 * Both run server-side on the read credential: proving an erasure happened
 * must not itself require the key that performs erasures, or the proof is only
 * available to the person who already knows the answer.
 */
export async function GET(request: Request) {
  const subject = new URL(request.url).searchParams.get("subject") ?? "";

  const [verify, recalled] = await Promise.all([
    verifyLedger().catch(() => null),
    subject
      ? recall(subject, { k: 10, includeUnverified: true })
          .then((r) => r.facts.filter((f) => f.subject_key === subject).length)
          .catch(() => null)
      : Promise.resolve(null),
  ]);

  return Response.json({ verify, recalled });
}
