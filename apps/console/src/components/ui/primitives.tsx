import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import { ACCENT_CLASSES, SEVERITY_PRESENTATION, type Severity } from "@/lib/brand";

/* ---------------------------------------------------------------- Card --- */

export function Card({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("rounded-lg border border-hairline bg-veil", className)} {...props} />;
}

export function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex items-center justify-between gap-3 border-b border-hairline px-5 py-3.5",
        className,
      )}
      {...props}
    />
  );
}

export function CardTitle({ className, ...props }: React.ComponentProps<"h2">) {
  return (
    <h2
      className={cn("font-display text-base tracking-[-0.02em] text-parchment", className)}
      {...props}
    />
  );
}

export function CardBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("px-5 py-4", className)} {...props} />;
}

/* ---------------------------------------------------------------- Stat --- */

/**
 * A number and what it means. `hint` is not decoration — the Overview's hero
 * stat is "facts trusted", and the hint is where "deliberately not facts
 * stored" gets said out loud.
 */
export function Stat({
  label,
  value,
  hint,
  accent = "parchment",
  size = "default",
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  accent?: keyof typeof ACCENT_CLASSES;
  size?: "default" | "hero";
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium tracking-wide text-dim uppercase">{label}</span>
      <span
        className={cn(
          "tabular font-display tracking-[-0.02em]",
          size === "hero" ? "text-4xl" : "text-2xl",
          ACCENT_CLASSES[accent].text,
        )}
      >
        {value}
      </span>
      {hint ? <span className="max-w-[36ch] text-xs text-moonstone">{hint}</span> : null}
    </div>
  );
}

/* -------------------------------------------------------- SeverityBadge --- */

export function SeverityBadge({ severity }: { severity: Severity }) {
  const presentation = SEVERITY_PRESENTATION[severity];
  const accent = ACCENT_CLASSES[presentation.accent];
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-sm border px-2 py-0.5 text-xs font-medium",
        accent.text,
        accent.fill,
        accent.border,
      )}
    >
      {presentation.label}
    </span>
  );
}

/* ----------------------------------------------------------- RegionChip --- */

/**
 * Flag-free on purpose: a region is a datacentre, not a country, and a flag
 * would imply a political claim the system is not making. Code in mono
 * (machine-authored, verifiable), name in body text.
 */
const REGION_NAMES: Record<string, string> = {
  "us-east-1": "N. Virginia",
  "eu-central-1": "Frankfurt",
  "ap-south-1": "Mumbai",
};

export function RegionChip({ region, className }: { region: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm border border-hairline bg-veil-hi px-2 py-0.5 text-xs",
        className,
      )}
    >
      <span className="font-mono text-parchment">{region}</span>
      {REGION_NAMES[region] ? <span className="text-dim">{REGION_NAMES[region]}</span> : null}
    </span>
  );
}

/* --------------------------------------------------------------- Button --- */

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded font-medium transition-colors duration-[120ms] ease-brand disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "border border-hairline bg-veil-hi text-parchment hover:bg-hairline",
        primary: "border border-synapse-edge bg-synapse-fill text-synapse hover:bg-synapse/20",
        ghost: "text-moonstone hover:bg-veil-hi hover:text-parchment",
        /* BRAND.md: signal red appears as a button background in EXACTLY one
         * place — the confirm control in Forget and Revoke. Naming the variant
         * `destroy` rather than `danger` keeps that literal rather than
         * aspirational. */
        destroy: "bg-signal font-semibold text-abyss hover:bg-signal/90",
      },
      size: {
        sm: "h-7 px-2.5 text-xs",
        default: "h-9 px-3.5 text-sm",
        lg: "h-11 px-5 text-base",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  // asChild renders the button's classes onto its child (typically a
  // next/link `<a>`) instead of wrapping it in a `<button>` — a link that
  // looks like a button should still navigate like a link, not sit inside
  // one and fight it for the click.
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}

/* ----------------------------------------------------------- EmptyState --- */

/**
 * Every empty screen instructs the next action (Phase 08.1) — no moods, no
 * shrugging illustrations. An empty state that only says "nothing here" wastes
 * the one moment the user is definitely looking for what to do next.
 */
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-start gap-2 rounded border border-dashed border-hairline px-5 py-6">
      <p className="font-display text-base text-parchment">{title}</p>
      <p className="max-w-prose text-sm text-moonstone">{children}</p>
      {action ? <div className="pt-1">{action}</div> : null}
    </div>
  );
}
