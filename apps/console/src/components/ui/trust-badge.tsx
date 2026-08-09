import { cn } from "@/lib/utils";
import { ACCENT_CLASSES, TRUST_PRESENTATION, type TrustShape, type TrustState } from "@/lib/brand";

/**
 * The shape is the accessible carrier, not the colour.
 *
 * BRAND.md: "Every trust state also carries a text label and a distinct shape
 * ... The system stays legible in greyscale, in forced-colors mode, and to a
 * colour-blind viewer." These are drawn with `currentColor` and explicit
 * geometry so they survive forced-colors, where background fills are replaced
 * by the OS but strokes are not.
 */
function TrustGlyph({ shape }: { shape: TrustShape }) {
  const common = {
    width: 10,
    height: 10,
    viewBox: "0 0 10 10",
    "aria-hidden": true as const,
    className: "shrink-0",
  };
  switch (shape) {
    case "circle":
      return (
        <svg {...common}>
          <circle cx="5" cy="5" r="3.5" fill="currentColor" />
        </svg>
      );
    case "circle-dashed":
      return (
        <svg {...common}>
          <circle
            cx="5"
            cy="5"
            r="3.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeDasharray="2.2 1.6"
          />
        </svg>
      );
    case "diamond":
      return (
        <svg {...common}>
          <path d="M5 1 L9 5 L5 9 L1 5 Z" fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      );
    case "diamond-split":
      // Half filled, half hollow — "two claims of comparable weight" made visual.
      return (
        <svg {...common}>
          <path d="M5 1 L9 5 L5 9 Z" fill="currentColor" />
          <path d="M5 1 L1 5 L5 9 Z" fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      );
    case "square-crossed":
      return (
        <svg {...common}>
          <rect
            x="1.2"
            y="1.2"
            width="7.6"
            height="7.6"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <path d="M1.2 1.2 L8.8 8.8" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      );
  }
}

export function TrustBadge({
  trust,
  className,
  showLabel = true,
}: {
  trust: TrustState;
  className?: string;
  showLabel?: boolean;
}) {
  const presentation = TRUST_PRESENTATION[trust];
  const accent = ACCENT_CLASSES[presentation.accent];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 text-xs font-medium",
        accent.text,
        accent.fill,
        accent.border,
        className,
      )}
      title={presentation.meaning}
    >
      <TrustGlyph shape={presentation.shape} />
      {/* The label is always in the DOM. When visually hidden it still reaches a
          screen reader, so the badge never degrades to colour-only. */}
      <span className={showLabel ? undefined : "sr-only"}>{presentation.label}</span>
    </span>
  );
}
