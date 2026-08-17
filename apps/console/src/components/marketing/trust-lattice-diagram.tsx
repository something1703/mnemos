"use client";

import * as React from "react";
import { ArrowRight } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { TrustBadge } from "@/components/ui/trust-badge";
import { cn } from "@/lib/utils";
import { TRUST_PRESENTATION } from "@/lib/brand";

const ACCENT_VAR: Record<string, string> = {
  synapse: "var(--synapse)",
  ember: "var(--ember)",
  signal: "var(--signal)",
  umbra: "var(--umbra)",
  parchment: "var(--parchment)",
};

/**
 * How a claim actually earns trust — the same five states `TrustBadge` draws
 * everywhere in the console, arranged as the real promotion paths
 * (`packages/engine/src/mnemos_engine/corroboration.py`), not a generic
 * funnel graphic. Every arrow here is a real code path, not an illustration
 * invented for the page.
 *
 * Plays as a sequence rather than fading in all at once — each node lights
 * up in promotion order, holds once the full path is lit, then resets and
 * repeats. A viewer should be able to read the story ("unverified needs two
 * sources to become corroborated, a trusted source promotes it outright, a
 * contradiction can still knock it into contested or quarantined") by
 * watching, not by decoding a static graph. Freezes on the fully-lit state
 * under `prefers-reduced-motion` instead of looping.
 */

// One flat timeline: the two rows are one continuous story, not two
// unrelated diagrams — a viewer watches the whole lattice light up before
// it resets, which is what makes it read as a system rather than two facts.
const STEPS = [
  "unverified",
  "arrow-1",
  "corroborated",
  "arrow-2",
  "trusted",
  "contested",
  "arrow-3",
  "quarantined",
] as const;

const HOLD_MS = 650;
const LONG_HOLD_MS = 1500; // where the story pauses to let a completed path sink in
const RESET_PAUSE_MS = 1000;
const LONG_HOLD_AFTER = new Set(["trusted", "quarantined"]);

type ElementState = "dim" | "current" | "lit";

function stateOf(index: number, stepIndex: number): ElementState {
  if (stepIndex < 0 || index > stepIndex) return "dim";
  if (index === stepIndex) return "current";
  return "lit";
}

export function TrustLatticeDiagram() {
  const reduced = useReducedMotion();
  const [stepIndex, setStepIndex] = React.useState(reduced ? STEPS.length - 1 : -1);

  React.useEffect(() => {
    if (reduced) return; // frozen on the fully-lit state, set above
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout>;

    function advance(i: number) {
      if (cancelled) return;
      setStepIndex(i);
      if (i < 0) {
        timeoutId = setTimeout(() => advance(0), HOLD_MS);
        return;
      }
      if (i >= STEPS.length) {
        timeoutId = setTimeout(() => advance(-1), RESET_PAUSE_MS);
        return;
      }
      const delay = LONG_HOLD_AFTER.has(STEPS[i]) ? LONG_HOLD_MS : HOLD_MS;
      timeoutId = setTimeout(() => advance(i + 1), delay);
    }

    advance(-1);
    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [reduced]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-center gap-3 md:gap-4">
        <Node
          trust="unverified"
          note="everything an LLM writes lands here"
          state={stateOf(0, stepIndex)}
        />
        <Arrow label="2 independent sources" state={stateOf(1, stepIndex)} />
        <Node trust="corroborated" state={stateOf(2, stepIndex)} />
        <Arrow label="system / operator source" state={stateOf(3, stepIndex)} />
        <Node trust="trusted" note="returned by recall" state={stateOf(4, stepIndex)} />
      </div>

      <div className="flex flex-wrap items-center justify-center gap-3 md:gap-4">
        <Node
          trust="contested"
          note="a comparable claim disagrees"
          state={stateOf(5, stepIndex)}
          small
        />
        <Arrow label="source revoked or gone stale" state={stateOf(6, stepIndex)} small />
        <Node
          trust="quarantined"
          note="withdrawn from recall"
          state={stateOf(7, stepIndex)}
          small
        />
      </div>

      <p className="mx-auto max-w-md text-center text-xs text-moonstone">
        Promotion needs a <em>different session</em> and a <em>different source
        origin</em> — the same repeated claim from one channel never counts twice.
      </p>
    </div>
  );
}

function Node({
  trust,
  note,
  state,
  small,
}: {
  trust: keyof typeof TRUST_PRESENTATION;
  note?: string;
  state: ElementState;
  small?: boolean;
}) {
  const glow = ACCENT_VAR[TRUST_PRESENTATION[trust].accent];

  return (
    <div className="flex flex-col items-center gap-1.5">
      <motion.div
        animate={
          state === "current"
            ? { scale: [1, 1.16, 1], opacity: 1 }
            : { scale: 1, opacity: state === "dim" ? 0.32 : 1 }
        }
        transition={
          state === "current" ? { duration: 0.5, ease: "easeOut" } : { duration: 0.35 }
        }
        style={state === "current" ? { filter: `drop-shadow(0 0 10px ${glow})` } : undefined}
      >
        <TrustBadge trust={trust} className={small ? "opacity-90" : "text-sm"} />
      </motion.div>
      {note ? (
        <span
          className={cn(
            "max-w-[14ch] text-center text-xs transition-colors duration-300",
            // A note that reads as ~1.8:1 contrast for up to 7s per loop is
            // not "de-emphasized," it is illegible — moonstone/50 still
            // clears large-text contrast at rest, and lands at full
            // moonstone once lit.
            state === "dim" ? "text-moonstone/50" : "text-moonstone",
          )}
        >
          {note}
        </span>
      ) : null}
    </div>
  );
}

function Arrow({
  label,
  state,
  small,
}: {
  label: string;
  state: ElementState;
  small?: boolean;
}) {
  return (
    <div className={cn("flex flex-col items-center gap-1", small && "opacity-80")}>
      <motion.div
        animate={
          state === "current"
            ? { x: [0, 4, 0], opacity: 1 }
            : { x: 0, opacity: state === "dim" ? 0.25 : 0.85 }
        }
        transition={
          state === "current"
            ? { duration: 0.6, repeat: Infinity, ease: "easeInOut" }
            : { duration: 0.35 }
        }
      >
        <ArrowRight
          className={cn("size-4", state === "dim" ? "text-dim" : "text-parchment")}
          aria-hidden
        />
      </motion.div>
      <span
        className={cn(
          "max-w-[12ch] text-center text-xs transition-colors duration-300",
          state === "dim" ? "text-moonstone/50" : "text-moonstone",
        )}
      >
        {label}
      </span>
    </div>
  );
}
