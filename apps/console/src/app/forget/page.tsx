import { getHolds } from "@/lib/api/queries";
import { apiConfigured } from "@/lib/api/server";
import { EmptyState } from "@/components/ui/primitives";
import { ForgetFlow } from "./forget-flow";

export const dynamic = "force-dynamic";

/**
 * Forget — the flagship flow.
 *
 * Active legal holds are fetched on the server and handed to the client so the
 * hold check can be surfaced BEFORE the button rather than after (Phase 08.2
 * is explicit about this). The Warden refuses a held subject regardless — that
 * is the real enforcement — but discovering it only by pressing a destructive
 * button teaches the user that the button is safe to press and see, which is
 * the wrong lesson for the one control that must never be pressed casually.
 */
export default async function ForgetPage() {
  if (!apiConfigured()) {
    return (
      <div className="flex flex-col gap-6">
        <Header />
        <EmptyState title="Not connected">
          Set <span className="font-mono">MNEMOS_API_URL</span> to use this flow.
        </EmptyState>
      </div>
    );
  }

  const holds = await getHolds()
    .then((r) => r.holds.filter((h) => h.active))
    .catch(() => []);

  return (
    <div className="flex flex-col gap-6">
      <Header />
      <ForgetFlow
        activeHolds={holds.map((h) => ({
          subject_key: h.subject_key,
          matter_reference: h.matter_reference,
        }))}
      />
    </div>
  );
}

function Header() {
  return (
    <header className="flex flex-col gap-1">
      <h1 className="font-display text-3xl tracking-[-0.02em]">Forget</h1>
      <p className="max-w-prose text-sm text-moonstone">
        Erase a subject&rsquo;s memory, with proof that it happened. Every mode previews exactly
        what dies before anything does, and a legal hold stops the flow here rather than at the
        database.
      </p>
    </header>
  );
}
