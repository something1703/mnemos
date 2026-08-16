"use client";

import { motion, useReducedMotion, type Variants } from "motion/react";

/**
 * A section that fades and rises into place the first time it scrolls into
 * view, once, then leaves it alone. The one piece of choreography reused
 * across the landing page, how-it-works, and the console's own dashboard
 * cards, so entrances read as one system rather than a different animation
 * per screen. No-ops entirely under `prefers-reduced-motion`.
 */
const variants: Variants = {
  hidden: { opacity: 0, y: 14 },
  visible: { opacity: 1, y: 0 },
};

export function Reveal({
  children,
  delay = 0,
  className,
  as = "div",
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
  as?: "div" | "li";
}) {
  const reduced = useReducedMotion();
  const Component = motion[as];

  if (reduced) {
    const Plain = as;
    return <Plain className={className}>{children}</Plain>;
  }

  return (
    <Component
      className={className}
      initial="hidden"
      whileInView="visible"
      viewport={{ once: true, margin: "-40px" }}
      variants={variants}
      transition={{ duration: 0.5, delay, ease: [0.2, 0.7, 0.3, 1] }}
    >
      {children}
    </Component>
  );
}
