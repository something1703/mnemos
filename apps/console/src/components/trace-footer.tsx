import { getLedger } from "@/lib/api/queries";
import { apiConfigured } from "@/lib/api/server";
import { MemoryTrace, type TraceCommit } from "@/components/viz/memory-trace";
import type { LedgerOp } from "@/lib/brand";

/**
 * The footer strip: the tenant's real ledger commits, newest on the right.
 *
 * A server component reading the live chain — not a decorative loop. If the
 * API is unreachable the strip says so rather than animating a lie, because a
 * fake heartbeat on an audit product is worse than a visible gap.
 */
export async function TraceFooter() {
  if (!apiConfigured()) {
    return (
      <footer className="border-t border-hairline bg-veil/40 px-4 py-2 text-xs text-moonstone">
        Memory trace idle — set <span className="font-mono">MNEMOS_API_URL</span> to connect it to
        a live ledger.
      </footer>
    );
  }

  let commits: TraceCommit[] = [];
  let error: string | null = null;
  try {
    const page = await getLedger(48);
    commits = page.entries
      .slice()
      .reverse()
      .map((entry) => ({
        shard: entry.shard_id,
        seq: entry.seq,
        op: entry.op as LedgerOp,
        rowHash: entry.entry_hash,
        at: entry.committed_at,
      }));
  } catch (cause) {
    error = cause instanceof Error ? cause.message : "unknown error";
  }

  return (
    <footer className="border-t border-hairline bg-veil/40">
      <div className="flex items-center gap-4 px-4 py-1.5">
        <span className="shrink-0 text-xs font-medium tracking-wide text-dim uppercase">
          Memory trace
        </span>
        <div className="min-w-0 flex-1 overflow-x-auto">
          {error ? (
            <p className="py-3 text-xs text-signal">Ledger unreachable — {error}</p>
          ) : (
            <MemoryTrace commits={commits} height={44} />
          )}
        </div>
      </div>
    </footer>
  );
}
