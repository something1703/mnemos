"use client";

import { ArrowDown, ArrowRight } from "lucide-react";
import { motion, useReducedMotion, type Variants } from "motion/react";
import { cn } from "@/lib/utils";

interface Stage {
  title: string;
  detail: string;
  accent: "ember" | "synapse" | "signal" | "umbra" | "parchment";
  llm?: boolean;
  /** Only `remember()` and `recall()` are real function identifiers; "the
   * Warden", "the ledger", "sleep cycle", and "semantic facts" are
   * human-written plane names. The Mono-Is-Semantic rule (DESIGN.md) says
   * mono marks a machine-authored, verifiable value — applying it to both
   * kinds uniformly was exactly the violation this flag exists to prevent. */
  mono?: boolean;
}

const WRITE_PATH: Stage[] = [
  {
    title: "remember()",
    detail: "an episode is written, encrypted, homed to a region — zero AI work",
    accent: "ember",
    mono: true,
  },
  {
    title: "sleep cycle",
    detail: "distills, corroborates, promotes — async, on a schedule",
    accent: "ember",
    llm: true,
  },
  {
    title: "semantic facts",
    detail: "trust starts at unverified and is earned from here",
    accent: "umbra",
  },
];

const READ_PATH: Stage[] = [
  {
    title: "recall()",
    detail: "hybrid search, trust-gated, logged",
    accent: "synapse",
    mono: true,
  },
];

const GOVERNANCE: Stage[] = [
  {
    title: "the Warden",
    detail: "residency, legal hold, erasure, revocation — no model in this process",
    accent: "signal",
  },
];

const LEDGER: Stage = {
  title: "the ledger",
  detail: "every state change appends a hash-chained row, in the same transaction",
  accent: "parchment",
};

const ACCENT_DOT: Record<Stage["accent"], string> = {
  ember: "bg-ember",
  synapse: "bg-synapse",
  signal: "bg-signal",
  umbra: "bg-umbra",
  parchment: "bg-moonstone",
};

const cardVariants: Variants = {
  hidden: { opacity: 0, y: 10 },
  visible: { opacity: 1, y: 0 },
};

/**
 * What actually happens between `remember()` and `recall()`, drawn as the
 * real pipeline rather than a generic "AI memory" graphic — this is the
 * shape `packages/engine`, `services/sleep-cycle`, and `packages/warden`
 * are, not an illustration invented for this page.
 */
export function LifecycleDiagram() {
  const reduced = useReducedMotion();

  return (
    <div className="flex flex-col items-center gap-2">
      <Row stages={WRITE_PATH} />
      <ArrowDown className="size-4 text-dim" aria-hidden />
      <StageCard stage={LEDGER} wide />
      <ArrowDown className="size-4 text-dim" aria-hidden />
      <Row stages={READ_PATH} />
      <div className="my-2 h-px w-24 bg-hairline" aria-hidden />
      <Row stages={GOVERNANCE} />
      <p className="mt-2 max-w-md text-center text-xs text-moonstone">
        Every box above the line appends to the ledger on every state change — the
        Warden included. Nothing skips it.
      </p>
    </div>
  );

  function Row({ stages }: { stages: Stage[] }) {
    return (
      <div className="flex flex-wrap items-stretch justify-center gap-3">
        {stages.map((s, i) => (
          <div key={s.title} className="flex items-center gap-3">
            <StageCard stage={s} />
            {i < stages.length - 1 ? (
              <ArrowRight className="size-4 shrink-0 text-dim" aria-hidden />
            ) : null}
          </div>
        ))}
      </div>
    );
  }

  function StageCard({ stage, wide }: { stage: Stage; wide?: boolean }) {
    return (
      <motion.div
        className={cn(
          "flex flex-col gap-1 rounded-lg border border-hairline bg-veil px-4 py-3",
          wide ? "w-full max-w-sm text-center" : "w-52",
          wide && "items-center",
        )}
        initial={reduced ? undefined : "hidden"}
        whileInView={reduced ? undefined : "visible"}
        viewport={{ once: true }}
        variants={cardVariants}
        transition={{ duration: 0.4 }}
      >
        <div className={cn("flex items-center gap-2", wide && "justify-center")}>
          <span className={cn("size-2 shrink-0 rounded-full", ACCENT_DOT[stage.accent])} aria-hidden />
          <span className={cn("text-sm text-parchment", stage.mono ? "font-mono" : "font-display")}>
            {stage.title}
          </span>
          {stage.llm ? (
            <span className="rounded-sm border border-hairline px-1.5 py-px text-xs tracking-wide text-moonstone uppercase">
              model
            </span>
          ) : null}
        </div>
        <p className="text-xs text-moonstone">{stage.detail}</p>
      </motion.div>
    );
  }
}
