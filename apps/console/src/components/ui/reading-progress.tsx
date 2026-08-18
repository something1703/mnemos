"use client";

import { motion, useScroll, useSpring, useReducedMotion } from "motion/react";

/**
 * A slim, factual readout of how far through the page you are — not
 * decoration, the same "brand lives in precise details" instinct DESIGN.md
 * applies everywhere else, aimed at a Read surface's actual job. Driven
 * entirely by the reader's own scroll position, so it stays on even under
 * `prefers-reduced-motion`: the bar's width is data the reader is actively
 * producing, not an animation playing at them. Only the smoothing spring
 * that makes it glide rather than jump is what reduced motion turns off.
 */
export function ReadingProgress() {
  const reduced = useReducedMotion();
  const { scrollYProgress } = useScroll();
  const smoothed = useSpring(scrollYProgress, { stiffness: 400, damping: 40, restDelta: 0.001 });

  return (
    <motion.div
      aria-hidden
      className="pointer-events-none fixed inset-x-0 top-0 z-40 h-[2px] origin-left bg-synapse"
      style={{ scaleX: reduced ? scrollYProgress : smoothed }}
    />
  );
}
