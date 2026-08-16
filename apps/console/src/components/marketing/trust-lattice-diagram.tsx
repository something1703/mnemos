"use client";

import { ArrowRight } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { TrustBadge } from "@/components/ui/trust-badge";
import { TRUST_PRESENTATION } from "@/lib/brand";

/**
 * How a claim actually earns trust — the same five states `TrustBadge` draws
 * everywhere in the console, arranged as the real promotion paths
 * (`packages/engine/src/mnemos_engine/corroboration.py`), not a generic
 * funnel graphic. Every arrow here is a real code path, not an illustration
 * invented for the page.
 */
export function TrustLatticeDiagram() {
  const reduced = useReducedMotion();

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-center gap-3 md:gap-4">
        <Node trust="unverified" note="everything an LLM writes lands here" />
        <Arrow label="2 independent sources" />
        <Node trust="corroborated" />
        <Arrow label="system / operator source" />
        <Node trust="trusted" note="returned by recall" />
      </div>

      <div className="flex flex-wrap items-center justify-center gap-3 md:gap-4">
        <Node trust="contested" note="a comparable claim disagrees" small />
        <Arrow label="source revoked or gone stale" small />
        <Node trust="quarantined" note="withdrawn from recall" small />
      </div>

      <p className="mx-auto max-w-md text-center text-xs text-dim">
        Promotion needs a <em>different session</em> and a <em>different source
        origin</em> — the same repeated claim from one channel never counts twice.
      </p>
    </div>
  );

  function Node({
    trust,
    note,
    small,
  }: {
    trust: keyof typeof TRUST_PRESENTATION;
    note?: string;
    small?: boolean;
  }) {
    return (
      <motion.div
        className="flex flex-col items-center gap-1.5"
        initial={reduced ? undefined : { opacity: 0, y: 8 }}
        whileInView={reduced ? undefined : { opacity: 1, y: 0 }}
        viewport={{ once: true }}
        transition={{ duration: 0.4 }}
      >
        <TrustBadge trust={trust} className={small ? "opacity-90" : "text-sm"} />
        {note ? <span className="max-w-[14ch] text-center text-[11px] text-dim">{note}</span> : null}
      </motion.div>
    );
  }

  function Arrow({ label, small }: { label: string; small?: boolean }) {
    return (
      <div className={`flex flex-col items-center gap-1 ${small ? "opacity-80" : ""}`}>
        <ArrowRight className="size-4 text-dim" aria-hidden />
        <span className="max-w-[12ch] text-center text-[10px] text-dim">{label}</span>
      </div>
    );
  }
}
