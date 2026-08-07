# Phase 01 — Foundations & Brand

**Objective:** A working monorepo with CI, all accounts provisioned, and a
locked brand system (theme tokens + logo) that every later phase consumes.
Nothing visual gets improvised after this phase.

## Inputs needed from the user — ASK BEFORE STARTING
1. GitHub org/username and the repo name to create (suggest: `mnemos`).
2. Confirmation they've signed up at cockroachlabs.cloud (free tier, no card).
3. Confirmation they have an AWS account and can create IAM users.
4. Team name + tagline preference for the brand (offer the default below).

## Sub-phase 1.1 — Repository & tooling
- [ ] Create public GitHub repo, Apache-2.0 LICENSE, repo About shows license.
- [ ] Monorepo skeleton per MASTER_PLAN conventions — note `packages/warden`
      and `redteam/` exist from day one, even if empty with a README stating
      their purpose. The structure should tell the story before the code does.
- [ ] Python: uv workspace; ruff + mypy (strict on `packages/`) configured.
- [ ] Node: pnpm workspace for `apps/console`.
- [ ] GitHub Actions: lint + typecheck + test on PR (matrix: engine, warden,
      api; console added in Phase 08). gitleaks job on every push.
- [ ] `.env.example`, `.gitignore`, CODEOWNERS, PR template that requires the
      phase + sub-phase number and a "which invariant does this touch?" line.
**Accept:** fresh clone → `make setup && make test` passes in <5 min.

## Sub-phase 1.2 — Accounts & access checklist
- [ ] CockroachDB Cloud org created; note org name in `docs/accounts.md`.
- [ ] AWS: dedicated IAM user/role for the project (NOT root), console access
      verified. Home region decided (default: `us-east-1`) and recorded, plus
      the two simulated jurisdictions used later for residency demos
      (default: `eu-central-1`, `ap-south-1`).
- [ ] Slack: join Cockroach Labs community Slack.
- [ ] Devpost: registered for the hackathon.
**Accept:** `docs/accounts.md` lists every account, owner, and region. No
credential values in the file.

## Sub-phase 1.3 — Brand definition (LOCKED after this phase)

Brand name **Mnemos** (from Mnemosyne, Greek goddess of memory, mother of the
Muses — memory as the parent of all capability; one line of this lore goes in
the README and nowhere else).

**Positioning line:** *Accountable Memory for Agents.*
**Pitch line:** *Agents don't fail when they're wrong. They fail when they
forget — or when they remember what they never should have.*

**Design direction — "the engram at night":** long-exposure neuron-microscopy
as the visual metaphor: deep indigo depths, bioluminescent traces.
Deliberately NOT the generic near-black + single acid accent. We use a
**four-accent semantic system** where color always encodes a system operation,
plus layered translucent surfaces instead of flat black.

**Palette (tokens — copy into `brand/tokens.css` and Tailwind config):**
- `--abyss: #12172B` — app background
- `--veil: #232B45` — raised surfaces / cards
- `--moonstone: #A8B3CF` — secondary text
- `--parchment: #EDEFF7` — primary text
- `--synapse: #6FE3D2` — cool accent: **recall**, live data, links
- `--ember: #F2A65A` — warm accent: **consolidation**, strength, the sleep cycle
- `--signal: #E4586B` — **destruction only**: forget, revoke, critical findings
- `--umbra: #8B7FD4` — **doubt**: unverified, quarantined, contested, low-trust

**Semantics rule (law):** synapse = reading memory, ember = writing memory,
signal = destroying memory, umbra = *doubting* memory. Colors are never
decorative. A judge should be able to read system state from color alone.
The umbra token is what makes the trust lattice legible — a quarantined fact
must be visually distinct from a believed one at a glance.

**Type:**
- Display: **Bricolage Grotesque** (chunky, characterful — headers, logotype)
- Body/UI: **Albert Sans**
- Data/mono: **Spline Sans Mono** (IDs, hashes, SQL, ledger, region codes)

**Signature element — the memory trace:** a horizontal hash-chain motif
(linked dots, each dot = a committed audit row), used as section divider,
loading indicator, and the live footer showing real ledger commits. It carries
real information:
- each dot is tinted by op class (synapse/ember/signal/umbra),
- the link segment renders **dashed** wherever an erasure occurred,
- **Merkle checkpoint rows render as a heavier anchor node** — visually
  "pinned," because they are literally anchored to WORM storage in S3,
- shard lanes stack vertically when a tenant's chain is sharded, converging
  at each checkpoint.

This is the one bold element; everything else stays quiet and disciplined.

- [ ] `brand/tokens.css` + `brand/tailwind.tokens.js` committed.
- [ ] `brand/BRAND.md` documenting all of the above, including the semantics
      law and the memory-trace information design.
**Accept:** a rendered sample page (`brand/sample.html`) shows tokens, type,
and a static memory trace with all four op colors + a checkpoint anchor; user
approves before the phase closes.

## Sub-phase 1.4 — Logo
Concept: an **M built from three linked nodes** (an engram): three filled
circles joined by two curved strokes forming the M silhouette; the second
stroke is subtly dashed at its end — forgetting is part of memory. Monochrome
first (parchment on abyss), synapse-tinted variant second.
- [ ] `brand/logo.svg` (mark), `brand/logotype.svg` (mark + "Mnemos" in
      Bricolage Grotesque), `brand/favicon.svg`.
- [ ] Renders cleanly at 16px, 32px, 512px; test in dark and light contexts.
**Accept:** user picks/approves the logo from 2–3 generated variants.

## Sub-phase 1.5 — Docs seed
- [ ] `docs/architecture.md` — the four-plane narrative (Fabric, Ledger,
      Warden, Custodian) + three diagrams: system topology, the write/recall
      data paths, and the trust lattice state machine.
- [ ] `docs/threat-model.md` — stub with the five attack classes Phase 10 will
      actually execute: memory poisoning, cross-tenant exfiltration, ledger
      tampering, residency violation, erasure evasion. Writing the threat
      model *before* the code is the point.
- [ ] `docs/decisions.md` — ADR log: ADR-001 monorepo, ADR-002 four-plane
      split, ADR-003 same-DB audit ledger, ADR-004 brand lock, ADR-005
      Warden-has-no-model.
- [ ] `docs/glossary.md` — episode, fact, provenance edge, trust state,
      deposition, blast radius, checkpoint, hold. One page. Judges will read
      it; so will every future contributor.

## Definition of Done
- [ ] CI green (lint + typecheck + test + gitleaks); clone-to-test under 5 min.
- [ ] All accounts live and recorded; three region codes chosen.
- [ ] Brand tokens, type, logo approved by the user and committed.
- [ ] ADR log started; threat model stub committed.
**Est: 2 days.**
