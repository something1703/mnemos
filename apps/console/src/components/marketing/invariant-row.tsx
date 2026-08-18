"use client";

import * as React from "react";
import { Check } from "lucide-react";
import { motion, useInView, useReducedMotion } from "motion/react";

/**
 * The invariant's number ticks over into a checkmark once it scrolls into
 * view — "each with a named test" made literal (a check is what a passing
 * test leaves behind), rather than the same generic fade-up every other
 * section on this site deliberately stopped using. Staggered by index so a
 * reader watches the five checks land in order, once, like a suite
 * finishing. Starts already checked under `prefers-reduced-motion`.
 */
export function InvariantRow({
  n,
  title,
  by,
  index,
}: {
  n: string;
  title: string;
  by: string;
  index: number;
}) {
  const reduced = useReducedMotion();
  const ref = React.useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, margin: "-80px" });
  const checked = reduced || inView;

  return (
    <div ref={ref} className="flex items-start gap-4 py-4">
      <span className="relative mt-0.5 flex size-4 shrink-0 items-center justify-center">
        <motion.span
          className="absolute font-mono text-xs text-dim"
          animate={{ opacity: checked ? 0 : 1, scale: checked ? 0.7 : 1 }}
          transition={{ duration: 0.25, delay: reduced ? 0 : index * 0.12 }}
        >
          {n}
        </motion.span>
        <motion.span
          className="absolute text-synapse"
          initial={false}
          animate={{ opacity: checked ? 1 : 0, scale: checked ? 1 : 0.5 }}
          transition={{ duration: 0.3, delay: reduced ? 0 : index * 0.12 + 0.12, ease: [0.2, 0.7, 0.3, 1] }}
        >
          <Check className="size-3.5" strokeWidth={2.75} aria-hidden />
        </motion.span>
      </span>
      <div className="flex flex-col gap-1">
        <p className="text-sm text-parchment">{title}</p>
        <p className="text-xs text-moonstone">{by}</p>
      </div>
    </div>
  );
}
