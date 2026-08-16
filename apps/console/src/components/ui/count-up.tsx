"use client";

import * as React from "react";
import { animate, useReducedMotion } from "motion/react";
import { formatCount } from "@/lib/utils";

/**
 * A stat value that counts up to its real number on mount instead of just
 * appearing. The number itself is never invented — it is the same integer
 * the server rendered, formatted the same way (`formatCount`); this only
 * changes how getting there looks. Skipped under `prefers-reduced-motion`,
 * and skipped for non-finite/negative values rather than animating nonsense.
 */
export function CountUp({ value, className }: { value: number; className?: string }) {
  const ref = React.useRef<HTMLSpanElement>(null);
  const reduced = useReducedMotion();

  React.useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (reduced || !Number.isFinite(value) || value < 0) {
      node.textContent = formatCount(value);
      return;
    }
    const controls = animate(0, value, {
      duration: Math.min(1.2, 0.3 + Math.log10(value + 1) * 0.25),
      ease: [0.2, 0.7, 0.3, 1],
      onUpdate: (v) => {
        if (node) node.textContent = formatCount(Math.round(v));
      },
    });
    return () => controls.stop();
  }, [value, reduced]);

  return (
    <span ref={ref} className={className}>
      {formatCount(value)}
    </span>
  );
}
