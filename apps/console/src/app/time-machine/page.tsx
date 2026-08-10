import { recall, recallAsOf, type RecallResult } from "@/lib/api/mcp";
import { getStats } from "@/lib/api/queries";
import { apiConfigured } from "@/lib/api/server";
import { EmptyState } from "@/components/ui/primitives";
import { TimeMachine } from "./time-machine";

export const dynamic = "force-dynamic";

/**
 * Time Machine — drag a scrubber over the temporal window and watch the same
 * question get a different answer.
 *
 * The GC boundary is stated, not hidden (Phase 08.2). CockroachDB's
 * `gc.ttlseconds` is what bounds `AS OF SYSTEM TIME`, and on the deployed
 * Basic cluster that is roughly 75 minutes — so most of history is simply not
 * addressable, and a scrubber that pretended otherwise would be lying about
 * the database. `recall_as_of` raises a typed OutsideTemporalWindow carrying
 * the real boundary; the client surfaces that message verbatim.
 */
export default async function TimeMachinePage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; t?: string }>;
}) {
  const params = await searchParams;
  const query = params.q?.trim() ?? "";

  if (!apiConfigured()) {
    return (
      <div className="flex flex-col gap-6">
        <Header />
        <EmptyState title="Not connected">
          Set <span className="font-mono">MNEMOS_API_URL</span> and{" "}
          <span className="font-mono">MNEMOS_API_KEY_READ</span> to scrub over real history.
        </EmptyState>
      </div>
    );
  }

  const stats = await getStats().catch(() => null);

  let now: RecallResult | null = null;
  let past: RecallResult | null = null;
  let pastError: string | null = null;
  const asOf = params.t ?? null;

  if (query) {
    now = await recall(query, { k: 8, includeUnverified: true }).catch(() => null);
    if (asOf) {
      try {
        past = await recallAsOf(query, asOf, { k: 8 });
      } catch (cause) {
        pastError = cause instanceof Error ? cause.message : "recall_as_of failed";
      }
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Header />
      <TimeMachine
        query={query}
        asOf={asOf}
        now={now}
        past={past}
        pastError={pastError}
        gcSeconds={4500}
        chainEntries={stats?.chain_entries ?? 0}
      />
    </div>
  );
}

function Header() {
  return (
    <header className="flex flex-col gap-1">
      <h1 className="font-display text-3xl tracking-[-0.02em]">Time Machine</h1>
      <p className="max-w-prose text-sm text-moonstone">
        The same question, asked of the past. Facts appear, change trust state, get superseded and
        get revoked — watching an agent&rsquo;s mind change over time is the point of this screen.
      </p>
    </header>
  );
}
