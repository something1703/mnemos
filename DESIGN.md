---
name: Mnemos
description: Accountable memory for agents — the engram at night
colors:
  abyss: "#12172b"
  veil: "#232b45"
  veil-hi: "#2d3752"
  hairline: "#36415f"
  parchment: "#edeff7"
  moonstone: "#a8b3cf"
  dim: "#6f7b9b"
  synapse: "#6fe3d2"
  synapse-fill: "rgb(111 227 210 / 12%)"
  synapse-edge: "rgb(111 227 210 / 38%)"
  ember: "#f2a65a"
  ember-fill: "rgb(242 166 90 / 12%)"
  ember-edge: "rgb(242 166 90 / 38%)"
  signal: "#e4586b"
  signal-fill: "rgb(228 88 107 / 12%)"
  signal-edge: "rgb(228 88 107 / 38%)"
  umbra: "#8b7fd4"
  umbra-fill: "rgb(139 127 212 / 14%)"
  umbra-edge: "rgb(139 127 212 / 42%)"
typography:
  display:
    fontFamily: "Bricolage Grotesque, ui-sans-serif, system-ui, sans-serif"
    fontSize: "clamp(1.6rem, 1.2rem + 2vw, 2.9rem)"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Albert Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: "Spline Sans Mono, ui-monospace, SF Mono, monospace"
    fontSize: "0.833rem"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.02em"
rounded:
  sm: "4px"
  md: "8px"
  lg: "14px"
spacing:
  1: "0.25rem"
  2: "0.5rem"
  3: "0.75rem"
  4: "1rem"
  6: "1.5rem"
  8: "2rem"
  12: "3rem"
components:
  button-primary:
    backgroundColor: "{colors.synapse-fill}"
    textColor: "{colors.synapse}"
    rounded: "{rounded.md}"
    padding: "0 0.875rem"
    height: "2.25rem"
  button-primary-hover:
    backgroundColor: "rgb(111 227 210 / 20%)"
  button-default:
    backgroundColor: "{colors.veil-hi}"
    textColor: "{colors.parchment}"
    rounded: "{rounded.md}"
    padding: "0 0.875rem"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.moonstone}"
    rounded: "{rounded.md}"
  button-destroy:
    backgroundColor: "{colors.signal}"
    textColor: "{colors.abyss}"
    rounded: "{rounded.md}"
    padding: "0 0.875rem"
  card:
    backgroundColor: "{colors.veil}"
    rounded: "{rounded.lg}"
  badge:
    rounded: "{rounded.sm}"
    padding: "0.125rem 0.5rem"
---

# Design System: Mnemos

## Overview

**Creative North Star: "The Engram at Night"**

Long-exposure neuron microscopy: deep indigo depths with bioluminescent traces moving through them. This is a deliberate departure from the default AI-product look (near-black plus one acid accent) in two ways: surfaces are layered translucency rather than flat black, implying tiers beneath the one you're looking at — which is literally what the product is, memory in tiers — and color is a four-way semantic system, not a single decorative accent. Most products use color for emphasis; this one uses it for meaning, and a viewer who has never seen the product before should still be able to read system state — reading, writing, destroying, doubting — from a screenshot alone, without a legend.

The system is quiet everywhere except one place. The memory trace (a live hash-chain of linked dots in the console's footer) is the one bold, animated signature element; everything else — cards, type, spacing — stays disciplined so that one motif keeps its weight.

**Key Characteristics:**
- Deep indigo surfaces in three translucent layers (abyss → veil → veil-hi), never flat black
- Four accent colors, each meaning exactly one system operation, never used decoratively
- Flat by default — depth comes from tonal layering and hairline borders, not shadows
- Signal red (destruction) appears as a filled button background in exactly one place: the confirm control inside a destructive flow
- Motion is restrained and purposeful (a memory trace draws in, a stat counts up to its real value) and fully suppressed under `prefers-reduced-motion`

## Colors

The palette reads as three surface layers, three text weights, and four meaning-bound accents — nothing decorative, nothing added because a screen felt empty.

### Primary
- **Synapse — bioluminescent teal** (`#6FE3D2`): reading memory. Recall results, live/real-time data, links, the "trusted"/"corroborated" trust states, the primary button. The system's single most-used accent because reading is the system's single most common operation.

### Secondary
- **Ember — warm amber** (`#F2A65A`): writing memory. Consolidation, the sleep cycle, strength/reinforcement, active legal holds — anything mid-process or in-flight.

### Tertiary
- **Signal — desaturated red** (`#E4586B`): destroying memory. Forget, revoke, shred, critical findings. Reserved so tightly that its one filled-button appearance (the destructive-confirm control) reads as a genuine warning, not house style.
- **Umbra — muted violet** (`#8B7FD4`): doubting memory. Unverified and contested trust states, quarantine, decay. This is the trust lattice's whole reason for existing as a fifth color rather than a binary trusted/untrusted: an unverified fact must look visibly different from a believed one, at a glance.

### Neutral
- **Abyss** (`#12172B`): the app background — the deepest layer.
- **Veil** (`#232B45`): raised surfaces, card backgrounds — one step up from the background.
- **Veil-hi** (`#2D3752`): hover/active surface — one step up from veil, used only as a state, never at rest.
- **Hairline** (`#36415F`): every 1px border and divider in the system.
- **Parchment** (`#EDEFF7`): primary text (14.8:1 against abyss).
- **Moonstone** (`#A8B3CF`): secondary text (7.4:1).
- **Dim** (`#6F7B9B`): tertiary text and disabled state only (3.6:1) — never body text, since it falls under the 4.5:1 floor the system otherwise holds everywhere.

### Named Rules
**The One Meaning Rule.** A color is never chosen for how it looks on a given screen. If a new element needs a color, it needs a clearer statement of what system operation it represents first — "needs some color" is not a valid reason to reach for any of the four.

**The Signal Scarcity Rule.** Signal red is a filled button background in exactly one place across the whole product: the confirm control inside the Forget and Revoke flows. Everywhere else it is text, a badge outline, or a status dot — never a second filled button, or the one true destructive action stops reading as different from a merely emphasized one.

## Typography

**Display Font:** Bricolage Grotesque (fallback: ui-sans-serif, system-ui)
**Body Font:** Albert Sans (fallback: ui-sans-serif, system-ui)
**Label/Mono Font:** Spline Sans Mono (fallback: ui-monospace, SF Mono)

**Character:** A chunky, characterful display face paired with a quiet, highly legible body face — deliberately keeping a governance product from reading like a compliance PDF, without the display face ever appearing at body sizes.

### Hierarchy
- **Display** (600, `clamp(1.6rem, 1.2rem + 2vw, 2.9rem)`, 1.15 line-height, −0.02em tracking): page titles, hero numbers, section headers. Bricolage Grotesque only — it never appears in a badge, a table cell, or a hint line.
- **Title** (600, 1rem, 1.15 line-height, −0.02em tracking): card headers (`CardTitle`).
- **Body** (400, 1rem, 1.55 line-height): all prose, labels, descriptions. Albert Sans.
- **Label/Mono** (500, 0.833rem, 0.02em tracking): every machine-authored or verifiable value — hashes, ledger sequence numbers, region codes, database roles, subject keys. Spline Sans Mono.

### Named Rules
**The Mono-Is-Semantic Rule.** Monospace never means "looks technical." It marks a value a machine wrote and a value the system can verify — a hash, a `shard/seq` pair, a region code. If a human wrote it (a finding's summary, a recommendation, a UX copy string), it is never set in mono, however technical the subject.

## Layout

Console pages sit inside a fixed nav rail (14rem wide, hidden below `md`) plus a fluid main column (`px-5 py-6` mobile, `px-8` at `md`+). Cards stack in CSS grid, most commonly `md:grid-cols-2` or `xl:grid-cols-4` for stat rows, collapsing to one column below `md`. The marketing site (landing, how-it-works) centers content in a `max-w-4xl`–`max-w-6xl` column depending on section, with generous vertical rhythm between sections (`py-16`–`py-24`) and a full-bleed alternating background (`bg-veil/20`) to separate sections without a hard rule. Spacing steps: 0.25rem, 0.5rem, 0.75rem, 1rem, 1.5rem, 2rem, 3rem — used consistently rather than arbitrary pixel values.

## Elevation & Depth

Flat by default. Depth comes from tonal layering (abyss → veil → veil-hi, each one step lighter) and a single hairline border — not shadows. The one exception is floating overlay content that must visually detach from the page it sits above: a tooltip or a dropdown menu carries `shadow-lg`/`shadow-xl`, because tonal layering alone doesn't read as "in front of" content it's overlapping. Focus state is a ring, not a shadow (`0 0 0 2px abyss, 0 0 0 4px synapse`), applied globally via `:focus-visible` so every interactive element gets it without a component remembering to add one.

### Named Rules
**The Overlay-Only Shadow Rule.** A card at rest never carries a shadow — its depth is tonal layering. Shadow is reserved for content that must read as floating above the page (tooltips, popovers, dropdown menus).

## Shapes

Three radius steps: 4px (`sm` — badges, small chips), 8px (default — buttons, inputs), 14px (`lg` — cards, the largest containers). Borders are always 1px hairline; nothing in the system uses a heavier or colored border to imply emphasis — emphasis is a fill or text-color change, never a thicker line.

## Components

### Buttons
- **Shape:** 8px radius (default).
- **Default:** hairline border, veil-hi background, parchment text — the workhorse variant for ordinary actions.
- **Primary:** synapse-tinted (12% fill, 38%-alpha border, synapse text) — used for the single most important action on a screen (e.g. "Open the console").
- **Ghost:** no border or fill at rest, moonstone text, veil-hi background only on hover — for secondary/tertiary actions that shouldn't compete visually.
- **Destroy:** the *only* variant with a solid fill (signal red background, abyss text, semibold) — reserved for exactly the destructive-confirm control described in the Signal Scarcity rule.
- **Hover/Focus:** all variants darken or fill slightly on hover (120ms, the system's fast-transition duration); focus-visible adds the global ring.

### Badges (Trust states)
- **Style:** 4px radius, hairline-alpha border tinted to the state's accent, 12–14%-opacity fill, accent-colored text.
- **State:** five trust states, each with BOTH a color and a distinct geometric shape (filled circle = trusted, dashed circle = corroborated, diamond = unverified, split diamond = contested, crossed square = quarantined) — the shape is load-bearing, not decorative, so the system stays legible in greyscale, forced-colors mode, and to a colorblind viewer.

### Cards / Containers
- **Corner Style:** 14px radius (`lg`).
- **Background:** veil, one step up from the abyss background.
- **Shadow Strategy:** none at rest (see Elevation & Depth).
- **Border:** 1px hairline.
- **Internal Padding:** header `px-5 py-3.5` with a hairline bottom border; body `px-5 py-4`.

### Inputs / Fields
- **Style:** hairline border, veil-hi or transparent background depending on context, 8px radius.
- **Focus:** the global ring (2px abyss offset + 4px synapse), never a color-only border change.

### Navigation
- **Style:** a fixed-width rail, icon + label per item, moonstone text at rest and parchment + a 2px synapse left-edge indicator when the route is active. Destructive-flow links (Blast Radius, Forget) are visually separated below a divider under an "Irreversible" label — never peers of the operational nav items above them.

### The Memory Trace (signature component)
A horizontal SVG hash-chain: one dot per committed ledger row, tinted by operation class via the same four-accent system, dashed link segments wherever an erasure occurred, and a heavier diamond "anchor" node at each Merkle checkpoint where parallel shard lanes visually converge. On mount the trace draws in left-to-right; on a later poll, only genuinely new commits pop in (with a brief expanding-ring highlight), so a viewer sees the chain *grow* rather than silently re-rendering. This is the one place in the system permitted continuous, celebratory motion — everywhere else motion is a one-time state transition.

## Do's and Don'ts

### Do:
- **Do** use the four accents (synapse/ember/signal/umbra) only for their assigned meaning — reading, writing, destroying, doubting — never for arbitrary visual variety (e.g. never use umbra just because a card "needs some color").
- **Do** pair every color-coded state with a text label and, for trust states specifically, a distinct shape — color is never the sole carrier of meaning.
- **Do** set machine-authored/verifiable values (hashes, IDs, region codes, shard/seq pairs) in Spline Sans Mono; set everything a human wrote in Albert Sans.
- **Do** respect `prefers-reduced-motion` on every animation without exception — the memory trace, count-up numbers, the trust-lattice sequence, and the logo draw-in all have a static, settled end-state to freeze on.
- **Do** use active-voice, verb-led button labels ("Forget subject," "Revoke source") — never "Submit" or "OK."

### Don't:
- **Don't** give a card, section, or badge a shadow at rest — shadow is reserved for floating overlay content only (tooltips, dropdown menus).
- **Don't** introduce a fifth accent color or a new hue for any reason. If something needs a color the four don't cover, it needs a clearer statement of what it means, not a new token.
- **Don't** use signal red as a filled button background anywhere except the one destructive-confirm control per flow — a second filled-red button anywhere else defeats the scarcity that makes the real one legible as a warning.
- **Don't** soften destructive UI copy ("Oops, are you sure?") — say plainly what will be destroyed and that it cannot be undone where that is true.
