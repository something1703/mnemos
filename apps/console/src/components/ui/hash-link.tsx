"use client";

import * as React from "react";
import { cn, middleTruncate } from "@/lib/utils";

/**
 * A hash or id: mono, middle-truncated, copy on click. The full value stays in
 * the title attribute, so truncation never actually withholds it.
 *
 * Its own client component rather than living in primitives.tsx: everything
 * else there renders fine on the server, and marking that whole module
 * "use client" to satisfy one useState would push every Card and Stat into the
 * client bundle for no reason.
 */
export function HashLink({
  value,
  head = 8,
  tail = 6,
  className,
}: {
  value: string;
  head?: number;
  tail?: number;
  className?: string;
}) {
  const [copied, setCopied] = React.useState(false);
  return (
    <button
      type="button"
      title={`${value}\nClick to copy`}
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        });
      }}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm font-mono text-xs text-moonstone transition-colors hover:text-synapse",
        className,
      )}
    >
      {middleTruncate(value, head, tail)}
      <span aria-live="polite" className="text-xs text-synapse">
        {copied ? "copied" : ""}
      </span>
    </button>
  );
}
