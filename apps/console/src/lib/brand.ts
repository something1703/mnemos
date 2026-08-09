/**
 * The brand contract, typed.
 *
 * `brand/tailwind.tokens.js` is the source of truth for TRUST_PRESENTATION and
 * OP_PRESENTATION (ADR-004, locked at Phase 01.3). It is plain JS with no
 * types, so this module re-declares the same tables with the types the console
 * needs — and `brand.contract.test.ts` asserts the two never drift, which is
 * the only reason a second declaration is acceptable at all.
 *
 * The shapes matter as much as the colours: BRAND.md requires that colour is
 * never the sole carrier of meaning, so every trust state has a label AND a
 * distinct shape and stays readable in greyscale, in forced-colors mode, and
 * to a colour-blind viewer.
 */

export type Accent = "synapse" | "ember" | "signal" | "umbra" | "parchment";

export type TrustState =
  | "trusted"
  | "corroborated"
  | "unverified"
  | "contested"
  | "quarantined";

export type TrustShape =
  | "circle"
  | "circle-dashed"
  | "diamond"
  | "diamond-split"
  | "square-crossed";

export interface TrustPresentation {
  accent: Accent;
  label: string;
  shape: TrustShape;
  /** Why an agent should care — surfaced in tooltips, not decoration. */
  meaning: string;
}

export const TRUST_PRESENTATION: Record<TrustState, TrustPresentation> = {
  trusted: {
    accent: "synapse",
    label: "Trusted",
    shape: "circle",
    meaning: "A system or operator source vouched for this. Returned by recall.",
  },
  corroborated: {
    accent: "synapse",
    label: "Corroborated",
    shape: "circle-dashed",
    meaning:
      "Two independent sources agree — different session and different origin. Returned by recall.",
  },
  unverified: {
    accent: "umbra",
    label: "Unverified",
    shape: "diamond",
    meaning:
      "Written by a model and not yet corroborated. Excluded from recall unless explicitly requested.",
  },
  contested: {
    accent: "umbra",
    label: "Contested",
    shape: "diamond-split",
    meaning:
      "Another fact of comparable strength directly contradicts this one. Returned, but flagged.",
  },
  quarantined: {
    accent: "signal",
    label: "Quarantined",
    shape: "square-crossed",
    meaning: "Withdrawn from recall — a source it rested on was revoked, or it went stale.",
  },
};

/** The order the Overview's trust distribution stacks in: believed → doubted. */
export const TRUST_ORDER: TrustState[] = [
  "trusted",
  "corroborated",
  "unverified",
  "contested",
  "quarantined",
];

export type LedgerOp =
  | "remember"
  | "consolidate"
  | "reinforce"
  | "promote"
  | "demote"
  | "recall"
  | "decay"
  | "quarantine"
  | "revoke"
  | "forget"
  | "shred"
  | "hold"
  | "checkpoint";

export const OP_PRESENTATION: Record<LedgerOp, { accent: Accent; label: string }> = {
  remember: { accent: "ember", label: "Remembered" },
  consolidate: { accent: "ember", label: "Consolidated" },
  reinforce: { accent: "ember", label: "Reinforced" },
  promote: { accent: "synapse", label: "Promoted" },
  demote: { accent: "umbra", label: "Demoted" },
  recall: { accent: "synapse", label: "Recalled" },
  decay: { accent: "umbra", label: "Decayed" },
  quarantine: { accent: "umbra", label: "Quarantined" },
  revoke: { accent: "signal", label: "Revoked" },
  forget: { accent: "signal", label: "Forgotten" },
  shred: { accent: "signal", label: "Shredded" },
  hold: { accent: "ember", label: "Held" },
  checkpoint: { accent: "parchment", label: "Checkpoint" },
};

export type Severity = "info" | "warn" | "critical";

export const SEVERITY_PRESENTATION: Record<Severity, { accent: Accent; label: string }> = {
  info: { accent: "synapse", label: "Info" },
  warn: { accent: "ember", label: "Warn" },
  critical: { accent: "signal", label: "Critical" },
};

/**
 * Tailwind cannot see class names assembled at runtime, so every accent
 * variant is written out literally here and referenced by key. This is the
 * documented escape hatch for dynamic classes, and it is safer than a
 * safelist: an accent with no entry fails to compile rather than rendering
 * unstyled in production.
 */
export const ACCENT_CLASSES: Record<Accent, { text: string; fill: string; border: string }> = {
  synapse: {
    text: "text-synapse",
    fill: "bg-synapse-fill",
    border: "border-synapse-edge",
  },
  ember: { text: "text-ember", fill: "bg-ember-fill", border: "border-ember-edge" },
  signal: { text: "text-signal", fill: "bg-signal-fill", border: "border-signal-edge" },
  umbra: { text: "text-umbra", fill: "bg-umbra-fill", border: "border-umbra-edge" },
  parchment: {
    text: "text-parchment",
    fill: "bg-veil-hi",
    border: "border-hairline",
  },
};
