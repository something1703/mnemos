"use client";

import * as React from "react";
import { motion, useReducedMotion, AnimatePresence } from "motion/react";
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

function commitKey(c: TraceCommit): string {
  return `${c.shard}-${c.seq}`;
}

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
 * Motion encodes something real too, not just "loading is done": on first
 * mount the whole visible trace draws in left-to-right, and — because this
 * component keeps re-rendering on a poll (the footer refetches the ledger),
 * not just once — a commit that is NEW since the last render pops in with its
 * own highlight, so "the chain just grew" is a thing you see happen rather
 * than a number that quietly changed. Off entirely under
 * `prefers-reduced-motion`, matching `brand/tokens.css`'s global rule.
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
  const reduced = useReducedMotion();
  // "Store information from previous renders" (React's own sanctioned
  // pattern for this) rather than a ref-read-during-render or a setState
  // inside useEffect — both are disallowed under the newer hooks rules. When
  // `commits` changes identity, this branch fires synchronously during
  // render, before anything paints, so it never causes a visible extra
  // frame: React restarts the render right there rather than committing an
  // intermediate one. An empty starting set doubles as "first paint" — every
  // commit compares as new against nothing, which is the stagger-in a real
  // first paint wants.
  const [prevCommits, setPrevCommits] = React.useState(commits);
  const [previousKeys, setPreviousKeys] = React.useState<Set<string>>(() => new Set());
  if (commits !== prevCommits) {
    setPreviousKeys(new Set(prevCommits.map(commitKey)));
    setPrevCommits(commits);
  }
  const isFirstRender = previousKeys.size === 0 && commits === prevCommits;

  const newKeys = React.useMemo(
    () => new Set(commits.map(commitKey).filter((k) => !previousKeys.has(k))),
    [commits, previousKeys],
  );

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
        const key = commitKey(commit);
        const isNew = !reduced && newKeys.has(key);
        // Stagger the first paint left-to-right; a later poll's fresh rows pop
        // in together instead, since they did not arrive in reading order.
        const delay = isFirstRender ? i * 0.02 : 0;

        return (
          <g key={key}>
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
              <motion.rect
                x={x - 4.5}
                y={y - 4.5}
                width={9}
                height={9}
                fill={colour}
                stroke="var(--abyss)"
                strokeWidth={1.5}
                transform={`rotate(45 ${x} ${y})`}
                initial={isNew ? { opacity: 0, scale: 0 } : false}
                animate={isNew ? { opacity: 1, scale: 1 } : undefined}
                transition={{ delay, duration: 0.4, ease: [0.2, 0.7, 0.3, 1] }}
                style={{ transformOrigin: `${x}px ${y}px` }}
              />
            ) : (
              <motion.circle
                cx={x}
                cy={y}
                r={3.5}
                fill={colour}
                initial={isNew ? { opacity: 0, scale: 0 } : false}
                animate={isNew ? { opacity: 1, scale: 1 } : undefined}
                transition={{ delay, duration: 0.4, ease: [0.2, 0.7, 0.3, 1] }}
                style={{ transformOrigin: `${x}px ${y}px` }}
              />
            )}
            {isNew ? (
              <AnimatePresence>
                <motion.circle
                  cx={x}
                  cy={y}
                  r={3.5}
                  fill="none"
                  stroke={colour}
                  strokeWidth={1.5}
                  initial={{ opacity: 0.9, scale: 1 }}
                  animate={{ opacity: 0, scale: 3.2 }}
                  transition={{ delay: delay + 0.1, duration: 0.7, ease: "easeOut" }}
                />
              </AnimatePresence>
            ) : null}
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
