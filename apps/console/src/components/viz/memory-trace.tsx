"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { OP_PRESENTATION, type LedgerOp } from "@/lib/brand";

export interface TraceCommit {
  shard: number;
  seq: number;
  op: LedgerOp;
  rowHash: string;
  at: string;
}

const ACCENT_VAR: Record<string, string> = {
  synapse: "var(--synapse)",
  ember: "var(--ember)",
  signal: "var(--signal)",
  umbra: "var(--umbra)",
  parchment: "var(--parchment)",
};

/**
 * The memory trace — the brand's signature element, and an information display
 * rather than an ornament (BRAND.md).
 *
 * Every visual property encodes something real:
 *   - one dot per committed audit row, tinted by operation class
 *   - stacked lanes, one per ledger shard, because the chain genuinely is
 *     sharded and pretending otherwise would misrepresent throughput
 *   - a DASHED link wherever an erasure happened, because that is exactly what
 *     a hash chain looks like across a row whose content is gone
 *   - a heavier anchor node at each Merkle checkpoint, where lanes converge
 *
 * Motion is opt-out: the draw-in animation is suppressed entirely under
 * `prefers-reduced-motion`, which brand/tokens.css also enforces globally.
 */
export function MemoryTrace({
  commits,
  className,
  height = 56,
  onSelect,
}: {
  commits: TraceCommit[];
  className?: string;
  height?: number;
  onSelect?: (commit: TraceCommit) => void;
}) {
  const shards = React.useMemo(
    () => Array.from(new Set(commits.map((c) => c.shard))).sort((a, b) => a - b),
    [commits],
  );

  if (commits.length === 0) {
    return (
      <div
        className={cn("flex items-center px-4 text-xs text-dim", className)}
        style={{ height }}
        role="status"
      >
        No ledger commits yet — the trace fills in as memory is written.
      </div>
    );
  }

  const laneCount = Math.max(shards.length, 1);
  const laneHeight = height / (laneCount + 1);
  const step = 26;
  const width = Math.max(commits.length * step + 32, 240);

  return (
    <svg
      className={cn("block", className)}
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMinYMid meet"
      role="img"
      aria-label={`Memory trace: ${commits.length} recent ledger commits across ${laneCount} shard${
        laneCount === 1 ? "" : "s"
      }`}
    >
      {commits.map((commit, i) => {
        const laneIndex = Math.max(shards.indexOf(commit.shard), 0);
        const y = laneHeight * (laneIndex + 1);
        const x = 16 + i * step;
        const previous = i > 0 ? commits[i - 1] : null;
        const presentation = OP_PRESENTATION[commit.op] ?? OP_PRESENTATION.checkpoint;
        const colour = ACCENT_VAR[presentation.accent] ?? "var(--parchment)";
        const isCheckpoint = commit.op === "checkpoint";
        // An erasure breaks the visual continuity of the chain on purpose.
        const erased = previous
          ? previous.op === "forget" || previous.op === "shred" || previous.op === "revoke"
          : false;

        return (
          <g key={`${commit.shard}-${commit.seq}`}>
            {previous ? (
              <line
                x1={x - step}
                y1={laneHeight * (Math.max(shards.indexOf(previous.shard), 0) + 1)}
                x2={x}
                y2={y}
                stroke="var(--hairline)"
                strokeWidth={1.25}
                strokeDasharray={erased ? "3 3" : undefined}
              />
            ) : null}
            {isCheckpoint ? (
              <rect
                x={x - 4.5}
                y={y - 4.5}
                width={9}
                height={9}
                fill={colour}
                stroke="var(--abyss)"
                strokeWidth={1.5}
                transform={`rotate(45 ${x} ${y})`}
              />
            ) : (
              <circle cx={x} cy={y} r={3.5} fill={colour} />
            )}
            <title>{`${presentation.label} · shard ${commit.shard} / seq ${commit.seq}\n${commit.rowHash}`}</title>
            {onSelect ? (
              <circle
                cx={x}
                cy={y}
                r={9}
                fill="transparent"
                className="cursor-pointer"
                onClick={() => onSelect(commit)}
              />
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}
