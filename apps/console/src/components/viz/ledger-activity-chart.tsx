"use client";

import * as React from "react";
import { scaleLinear } from "d3-scale";
import { motion, useReducedMotion } from "motion/react";
import { OP_PRESENTATION, type LedgerOp } from "@/lib/brand";
import { formatCount } from "@/lib/utils";
import { cn } from "@/lib/utils";

const ACCENT_VAR: Record<string, string> = {
  synapse: "var(--synapse)",
  ember: "var(--ember)",
  signal: "var(--signal)",
  umbra: "var(--umbra)",
  parchment: "var(--parchment)",
};

export interface OpCount {
  op: LedgerOp;
  count: number;
}

/**
 * Real ledger entries, counted by operation and drawn as a horizontal bar —
 * "the number of live graphs" gap the console had (Phase 08 follow-up). Color
 * is not chosen for this chart; it is inherited from `OP_PRESENTATION`, the
 * same status mapping the memory trace and every badge in the console already
 * use, so a bar here means the same thing a dot means in the trace footer.
 *
 * Bars ≤24px thick, 4px rounded tip, 2px gaps, direct value labels — dataviz
 * skill's mark spec. One series (count), so no legend box is needed; the
 * chart title plus each bar's own op label already say what is plotted.
 */
export function LedgerActivityChart({
  counts,
  className,
}: {
  counts: OpCount[];
  className?: string;
}) {
  const reduced = useReducedMotion();
  const [hovered, setHovered] = React.useState<LedgerOp | null>(null);
  const sorted = React.useMemo(() => [...counts].sort((a, b) => b.count - a.count), [counts]);
  const max = Math.max(...sorted.map((c) => c.count), 1);
  const scale = scaleLinear().domain([0, max]).range([0, 100]);

  const barHeight = 22;
  const gap = 10;
  const height = sorted.length * (barHeight + gap) - gap;

  if (sorted.length === 0) {
    return <p className="text-sm text-moonstone">No ledger activity in this window.</p>;
  }

  return (
    <div className={cn("flex flex-col gap-0", className)} style={{ height }}>
      {sorted.map((row, i) => {
        const presentation = OP_PRESENTATION[row.op] ?? OP_PRESENTATION.checkpoint;
        const colour = ACCENT_VAR[presentation.accent];
        const widthPct = scale(row.count);
        const isHovered = hovered === row.op;

        return (
          <div
            key={row.op}
            className="group relative flex items-center gap-3"
            style={{ height: barHeight, marginBottom: i === sorted.length - 1 ? 0 : gap }}
            onPointerEnter={() => setHovered(row.op)}
            onPointerLeave={() => setHovered((h) => (h === row.op ? null : h))}
            onFocus={() => setHovered(row.op)}
            onBlur={() => setHovered((h) => (h === row.op ? null : h))}
            tabIndex={0}
            role="img"
            aria-label={`${presentation.label}: ${formatCount(row.count)}`}
          >
            <span className="w-24 shrink-0 truncate text-xs text-moonstone">
              {presentation.label}
            </span>
            <div className="relative h-full min-w-0 flex-1 rounded-sm bg-veil-hi/40">
              <motion.div
                className="h-full rounded-sm"
                style={{
                  background: colour,
                  opacity: isHovered ? 1 : 0.88,
                }}
                initial={reduced ? undefined : { width: 0 }}
                animate={{ width: `${widthPct}%` }}
                transition={{ duration: reduced ? 0 : 0.6, delay: i * 0.04, ease: [0.2, 0.7, 0.3, 1] }}
              />
            </div>
            <span
              className={cn(
                "w-10 shrink-0 text-right text-xs tabular transition-colors",
                isHovered ? "text-parchment" : "text-dim",
              )}
            >
              {formatCount(row.count)}
            </span>

            {isHovered ? (
              <div
                className="pointer-events-none absolute top-full left-24 z-10 mt-1 rounded border border-hairline bg-abyss px-2 py-1 text-xs whitespace-nowrap text-parchment shadow-lg"
                role="tooltip"
              >
                <span className="mr-1.5 inline-block h-2 w-2 rounded-full" style={{ background: colour }} />
                {formatCount(row.count)} {presentation.label.toLowerCase()}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
