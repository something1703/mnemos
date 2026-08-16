import "server-only";
import type { LedgerEntry } from "@/lib/api/queries";
import type { LedgerOp } from "@/lib/brand";
import type { OpCount } from "@/components/viz/ledger-activity-chart";
import type { Bucket } from "@/components/viz/activity-sparkline";

/** Real counts, not sampled or invented — every entry in the page passed in
 * is counted exactly once. */
export function countByOp(entries: LedgerEntry[]): OpCount[] {
  const counts = new Map<LedgerOp, number>();
  for (const entry of entries) {
    const op = entry.op as LedgerOp;
    counts.set(op, (counts.get(op) ?? 0) + 1);
  }
  return Array.from(counts, ([op, count]) => ({ op, count }));
}

/**
 * Buckets entries into `hours` equal-width hourly windows ending now, so a
 * quiet hour with genuinely zero commits renders as a real zero rather than
 * being skipped and implying continuity that was not observed.
 */
export function bucketByHour(entries: LedgerEntry[], hours = 24): Bucket[] {
  const now = Date.now();
  const hourMs = 3_600_000;
  const start = now - (hours - 1) * hourMs;
  const buckets: Bucket[] = Array.from({ length: hours }, (_, i) => ({
    at: new Date(start + i * hourMs).toISOString(),
    count: 0,
  }));

  for (const entry of entries) {
    const t = new Date(entry.committed_at).getTime();
    if (Number.isNaN(t) || t < start - hourMs) continue;
    const index = Math.floor((t - start) / hourMs);
    if (index >= 0 && index < buckets.length) {
      buckets[index].count += 1;
    } else if (index === buckets.length) {
      // Falls in the current, still-open hour.
      buckets[buckets.length - 1].count += 1;
    }
  }

  return buckets;
}
