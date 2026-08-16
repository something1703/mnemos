"use client";

import * as React from "react";
import { motion, useReducedMotion, type Variants } from "motion/react";
import { cn } from "@/lib/utils";

/**
 * The mark, inline — not `<img src="/brand/logo.svg">`. `motion` animates
 * elements it can see in the React tree; an `<img>` is opaque to it. The path
 * data below is a literal copy of `brand/logo.svg` (BRAND.md: "an M built
 * from three linked nodes... the right leg terminates in a dashed segment,
 * because forgetting is part of memory rather than a failure of it").
 *
 * `animate` draws the mark in stroke-by-stroke on mount — three nodes
 * appearing in sequence, the dashed terminus fading in last, so the one
 * design idea BRAND.md calls "the whole product in one stroke" is the first
 * thing a visitor's eye follows on the landing page. Off entirely under
 * `prefers-reduced-motion`, matching `brand/tokens.css`'s global rule for the
 * memory trace.
 */

const strokeVariants: Variants = {
  hidden: { pathLength: 0, opacity: 0 },
  visible: (delay: number) => ({
    pathLength: 1,
    opacity: 1,
    transition: { delay, duration: 0.5, ease: [0.2, 0.7, 0.3, 1] },
  }),
};

const nodeVariants: Variants = {
  hidden: { scale: 0, opacity: 0 },
  visible: (delay: number) => ({
    scale: 1,
    opacity: 1,
    transition: { delay, duration: 0.35, ease: [0.2, 0.7, 0.3, 1] },
  }),
};

export function Logo({
  className,
  size = 24,
  animate = false,
}: {
  className?: string;
  size?: number;
  animate?: boolean;
}) {
  const reduced = useReducedMotion();
  const draw = animate && !reduced;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      role="img"
      aria-label="Mnemos"
      className={cn("shrink-0", className)}
    >
      <g fill="none" stroke="currentColor" strokeWidth={5} strokeLinecap="round" strokeLinejoin="round">
        <motion.path
          d="M12 51 V21"
          initial={draw ? "hidden" : undefined}
          animate={draw ? "visible" : undefined}
          custom={0}
          variants={strokeVariants}
        />
        <motion.path
          d="M12 21 L32 43"
          initial={draw ? "hidden" : undefined}
          animate={draw ? "visible" : undefined}
          custom={0.15}
          variants={strokeVariants}
        />
        <motion.path
          d="M32 43 L52 21"
          initial={draw ? "hidden" : undefined}
          animate={draw ? "visible" : undefined}
          custom={0.3}
          variants={strokeVariants}
        />
        <motion.path
          d="M52 21 V37"
          initial={draw ? "hidden" : undefined}
          animate={draw ? "visible" : undefined}
          custom={0.45}
          variants={strokeVariants}
        />
        <motion.path
          d="M52 43 V51"
          strokeDasharray="0.1 9"
          opacity={0.85}
          initial={draw ? { opacity: 0 } : undefined}
          animate={draw ? { opacity: 0.85 } : undefined}
          transition={draw ? { delay: 0.7, duration: 0.4 } : undefined}
        />
      </g>
      <g fill="currentColor">
        <motion.circle
          cx={12}
          cy={21}
          r={5.5}
          initial={draw ? "hidden" : undefined}
          animate={draw ? "visible" : undefined}
          custom={0}
          variants={nodeVariants}
        />
        <motion.circle
          cx={32}
          cy={43}
          r={5.5}
          initial={draw ? "hidden" : undefined}
          animate={draw ? "visible" : undefined}
          custom={0.15}
          variants={nodeVariants}
        />
        <motion.circle
          cx={52}
          cy={21}
          r={5.5}
          initial={draw ? "hidden" : undefined}
          animate={draw ? "visible" : undefined}
          custom={0.3}
          variants={nodeVariants}
        />
      </g>
    </svg>
  );
}
