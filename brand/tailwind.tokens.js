/**
 * Tailwind bridge for brand/tokens.css. The CSS file is the source of truth
 * (ADR-004); this only exposes it to Tailwind's utility generator.
 *
 * Import into apps/console/tailwind.config.ts:
 *   import { mnemosTheme } from "../../brand/tailwind.tokens.js";
 *   export default { theme: { extend: mnemosTheme } };
 */

export const mnemosTheme = {
  colors: {
    abyss: "var(--abyss)",
    veil: "var(--veil)",
    "veil-hi": "var(--veil-hi)",
    hairline: "var(--hairline)",

    parchment: "var(--parchment)",
    moonstone: "var(--moonstone)",
    dim: "var(--dim)",

    // Semantic accents. Named for what they mean, never for what they look
    // like — `text-signal` should read as "destructive", not "reddish".
    synapse: {
      DEFAULT: "var(--synapse)",
      fill: "var(--synapse-fill)",
      edge: "var(--synapse-edge)",
    },
    ember: {
      DEFAULT: "var(--ember)",
      fill: "var(--ember-fill)",
      edge: "var(--ember-edge)",
    },
    signal: {
      DEFAULT: "var(--signal)",
      fill: "var(--signal-fill)",
      edge: "var(--signal-edge)",
    },
    umbra: {
      DEFAULT: "var(--umbra)",
      fill: "var(--umbra-fill)",
      edge: "var(--umbra-edge)",
    },
  },

  fontFamily: {
    display: "var(--font-display)",
    body: "var(--font-body)",
    mono: "var(--font-mono)",
  },

  fontSize: {
    xs: ["var(--step--1)", { lineHeight: "var(--leading-body)" }],
    base: ["var(--step-0)", { lineHeight: "var(--leading-body)" }],
    lg: ["var(--step-1)", { lineHeight: "var(--leading-body)" }],
    xl: ["var(--step-2)", { lineHeight: "var(--leading-tight)" }],
    "2xl": ["var(--step-3)", { lineHeight: "var(--leading-tight)" }],
    "3xl": ["var(--step-4)", { lineHeight: "var(--leading-tight)" }],
  },

  borderRadius: {
    sm: "var(--radius-sm)",
    DEFAULT: "var(--radius)",
    lg: "var(--radius-lg)",
  },

  transitionTimingFunction: { brand: "var(--ease)" },
  transitionDuration: { fast: "var(--dur-fast)", brand: "var(--dur)" },
};

/**
 * Trust states map to accents in exactly one place — here. Every surface that
 * renders a trust state imports this, so the console cannot drift from the
 * engine's vocabulary.
 *
 * `shape` exists because color is never the sole carrier of meaning.
 */
export const TRUST_PRESENTATION = {
  trusted: { accent: "synapse", label: "Trusted", shape: "circle" },
  corroborated: { accent: "synapse", label: "Corroborated", shape: "circle-dashed" },
  unverified: { accent: "umbra", label: "Unverified", shape: "diamond" },
  contested: { accent: "umbra", label: "Contested", shape: "diamond-split" },
  quarantined: { accent: "signal", label: "Quarantined", shape: "square-crossed" },
};

/** Ledger operations map to accents the same way. */
export const OP_PRESENTATION = {
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
