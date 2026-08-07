# Mnemos — brand system

Locked at Phase 01.3 (ADR-004). `brand/tokens.css` is the source of truth;
`brand/tailwind.tokens.js` exposes it to the console. Nothing visual is
improvised after this point.

## Positioning

**Name:** Mnemos — from Mnemosyne, Greek goddess of memory and mother of the
Muses: memory as the parent of all capability. That line appears once, in the
README, and nowhere else. Lore used twice is a theme; used three times it is a
costume.

**Positioning line:** Accountable Memory for Agents.

**Pitch line:** *Agents don't fail when they're wrong. They fail when they
forget — or when they remember what they never should have.*

## Design direction — "the engram at night"

Long-exposure neuron microscopy: deep indigo depths, bioluminescent traces.
Deliberately **not** the default AI-product look of near-black plus one acid
accent. Two departures carry the identity:

1. **Layered translucent surfaces** rather than flat black. Depth implies the
   tiers beneath the surface, which is what the product is.
2. **A four-accent semantic system** where color encodes a system operation.
   Most products use accent color for emphasis. We use it for meaning.

## The four accents (this is the law)

| Token | Hex | Means | Used for |
|---|---|---|---|
| `--synapse` | `#6FE3D2` | **reading** memory | recall, live data, links, trusted facts |
| `--ember` | `#F2A65A` | **writing** memory | consolidation, strength, the sleep cycle |
| `--signal` | `#E4586B` | **destroying** memory | forget, revoke, shred, critical findings |
| `--umbra` | `#8B7FD4` | **doubting** memory | unverified, contested, quarantined |

Colour is never decorative. A judge should be able to read system state from a
screenshot without a legend: teal is knowledge, amber is work, red is loss,
violet is uncertainty.

`--umbra` earns its place because the trust lattice is the security thesis. An
unverified fact must be visibly different from a believed one at a glance —
otherwise the corroboration gate is invisible, and an invisible defense
persuades nobody.

**Signal red is never used decoratively, and appears as a button background in
exactly one place: the confirm control in the Forget and Revoke flows.** If red
appears anywhere else, something is being destroyed or something is wrong.

### Colour is never the only signal

Every trust state also carries a text label and a distinct shape
(`TRUST_PRESENTATION` in `tailwind.tokens.js`): circle, dashed circle, diamond,
split diamond, crossed square. The system stays legible in greyscale, in
forced-colors mode, and to a colour-blind viewer. Contrast ratios against
`--abyss` are recorded in `tokens.css`; nothing below 4.5:1 is used for text.

## Type

| Role | Family | Why |
|---|---|---|
| Display | **Bricolage Grotesque** | Chunky and characterful; keeps a governance product from reading as a compliance PDF |
| Body / UI | **Albert Sans** | Quiet, high legibility at small sizes, wide weight range |
| Data / mono | **Spline Sans Mono** | IDs, hashes, SQL, region codes, ledger sequence numbers |

**Mono is semantic too:** it marks machine-authored, verifiable values. If a
human wrote it, it is not mono. A hash, a `crdb_region`, a shard/seq pair, and
a subject key are mono. A finding's summary is not.

## The signature element — the memory trace

A horizontal hash-chain motif: linked dots, each dot one committed audit row.
It appears as a section divider, a loading indicator, and — in the console
footer — a live feed of the tenant's real ledger commits.

It is an information display, not an ornament:

- each dot is tinted by operation class (see `OP_PRESENTATION`);
- the link segment renders **dashed** wherever an erasure occurred;
- **Merkle checkpoints render as a heavier anchor node** — visually pinned,
  because they are literally anchored to WORM storage in S3;
- when a tenant's chain is sharded, lanes stack vertically and converge at each
  checkpoint, so the sharding is legible rather than hidden.

This is the one bold element. Everything else stays quiet and disciplined.
Respects `prefers-reduced-motion`.

## Logo

An **M built from three linked nodes** — an engram. Three filled circles joined
by strokes forming the M silhouette; the right leg terminates in a **dashed**
segment, because forgetting is part of memory rather than a failure of it. That
dash is the entire product in one stroke, which is why it survives into the
favicon (there, as a single detached dot — a dash pattern turns to mud at 16px).

- `logo.svg` — mark, `currentColor`, inherits from context
- `logotype.svg` — mark + wordmark, parchment on transparent, synapse nodes
- `favicon.svg` — abyss tile, heavier strokes, legible at 16px

**Font caveat:** SVG embedded in Markdown cannot load webfonts, so
`logotype.svg` falls back through a stack. For video, print, or any asset where
the wordmark must be exact, set it in Bricolage Grotesque and convert to
outlines.

## Copy rules

- Active voice. Verbs on buttons: "Forget subject", "Revoke source", not
  "Submit" or "OK".
- Toasts state what happened and where the proof is:
  *"Forgotten — proof committed at #3/1471."*
- Empty states instruct the next action. No moods, no shrugging illustrations,
  no "Nothing here yet!"
- Never soften a destructive action with friendly language. A confirm dialog
  that says "Oops, are you sure?" is lying about the stakes.
- Say what is true and no more: "tamper-evident within one checkpoint epoch",
  not "tamper-proof". The language discipline in AGENTS.md applies to UI copy
  exactly as it applies to the README.
