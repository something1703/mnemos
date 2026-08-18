# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two audiences, by surface — confirmed by the user rather than inferred, because getting this wrong would misdirect every future design decision through this skill:

- **Persuade surfaces** (`/`, `/how-it-works`): a judge for the CockroachDB × AWS "Build with Agentic Memory" hackathon (submission deadline 2026-08-19), evaluating the project cold, in minutes, against a public Devpost rubric. They have not opened the console before landing here and may never open it at all — the landing page is where the argument has to land.
- **Operate surfaces** (`/console/*`, the nine dashboard screens): the fictional-but-specific operator persona the three demo verticals are built for — someone governing agent memory inside a regulated vertical, not a generic "user." Concretely: a cross-border clinic operator confirming a patient's raw record never left its home region (Continuity), a DevOps engineer discovering a poisoned runbook and tracing its blast radius (Contagion), and a consumer-finance compliance reviewer producing a regulator-facing deposition for a declined application (Deposition). These are not marketing personas, they are the literal scripts the three live demo tenants (clinic/ops/finance) run — walk any of them in the [live console](https://mnemos-beta.vercel.app/console).

## Product Purpose

Mnemos is accountable memory for AI agents: a memory layer that answers three questions no shipping agent-memory system answers today (see README.md):

1. **Where does this memory legally live?** — an agent recalling a record in one jurisdiction must not have moved the raw content across a border to do it.
2. **What did the agent believe, and why did it act?** — a decision needs a deposition (the exact facts as they stood, their provenance, hash-verified), not a log file.
3. **One poisoned fact entered memory — what did it touch?** — persistent prompt injection needs an enumerable, revocable blast radius, not an unbounded, undetectable contamination.

Success is a judge being able to verify all three against a real, live deployment rather than a slide.

## Positioning

The mechanism a neighboring product could not truthfully copy without becoming this one: vectors, the provenance graph, and the audit ledger live in **one transactionally consistent distributed database** (CockroachDB), not three services with a consistency gap between them. Concretely:

- Corroboration is arithmetic, not model judgement — a fact promotes only with two independent provenance signatures (different session **and** different source-trust category), computed as maximum bipartite matching, not a heuristic. The collusion threshold (2) is published, not implied to be infinite (`docs/redteam.md`).
- `revoke_source()` computes the transitive blast radius and revokes it in one serializable transaction, broadcast on a changefeed — containment as the real control, since prevention (prompt-level defenses) is stated in writing to fail eventually.
- `REGIONAL BY ROW` homes every episode to a jurisdiction; only policy-approved projections cross, logged with the policy that allowed it.
- The only component that can destroy memory (the Warden) contains no model — enforced structurally (IAM, CI import-graph check, a runtime self-check), not by policy.

## Operating Context

**The three demo verticals are the real operating scenarios**, each its own tenant on the same fabric:

- **Continuity** (clinic, residency pillar): episodic write in `eu-central-1`, recall from a different region returns the derived fact only, an erasure request against a legal hold fails loudly and explains why.
- **Contagion** (DevOps copilot, integrity pillar): a learned playbook lands quarantined until independently corroborated; a poisoned source is shown being *contained*, not prevented — the demo explicitly shows the defense's boundary (two colluding sources still promote a fact) before showing the cure (`blast_radius` → `revoke_source`).
- **Deposition** (consumer finance, accountability pillar): `explain(action_id)` reconstructs exactly what an agent believed at decision time, including a since-revoked fact, exported as HTML that verifies itself offline, network disconnected.

**Real, live infrastructure** — this is a working deployment, not a mockup, and the console is designed to prove that on screen:

- CockroachDB Cloud (Basic tier, `aws-us-east-1`).
- AWS: Lambda (the API, the sleep cycle), ECS Fargate (the Custodian, scheduled + alarm-triggered), S3 Object Lock (ledger anchoring, compliance-mode WORM), KMS (per-tenant envelope encryption / crypto-shred).
- The console (Next.js 16, this app) is built and passing but **not yet deployed publicly** — Vercel `login`/`link` is a step only the user can complete; there is no public URL yet.

## Capabilities and Constraints

**Four planes**, exactly one of which contains a model (README.md):

| Plane | Role | Contains an LLM? |
|---|---|---|
| The Fabric | episodic → semantic → procedural memory | no |
| The Ledger | sharded hash chains, Merkle checkpoints anchored to S3 Object Lock | no |
| The Warden | residency, legal hold, erasure, blast-radius revocation | no — IAM + CI + runtime self-check |
| The Custodian | runs official CockroachDB Agent Skills, files findings back into memory | yes — provider-agnostic (OpenAI today, ADR-014), sandboxed to read-only allowlisted tools |

**Five invariants**, each with a named test (`tests/invariants/`, `make invariants`): no LLM-driven process holds DELETE or governance privileges; every state-changing memory op appends a hash-chained audit row in the same transaction; no fact becomes recallable without provenance to at least one episode; memory rows never leave their home region; erasure is atomic across rows, vectors, and provenance or it does not happen.

**Constraint found and fixed this session, worth preserving as a fact future work should not regress**: `source_trust` used to be a caller-declared argument on the `remember`/`learn_skill` tools, which meant an injected agent could self-declare `operator` and skip corroboration entirely. `system`/`operator` now require an admin credential (`Principal.may_declare`); `agent`/`external` stay freely declarable since both land `unverified` regardless.

**Explicitly undecided**: no public GitHub org/repo URL is finalized (README carries a `<org>` placeholder) — do not invent one.

## Brand Commitments

`brand/BRAND.md` is locked (ADR-004, Phase 01.3) — binding, not a starting point for this skill's own design work. In force:

- Name: **Mnemos**. Positioning line: *Accountable Memory for Agents*. Pitch line: *"Agents don't fail when they're wrong. They fail when they forget — or when they remember what they never should have."*
- Four-accent semantic color system — synapse (teal, reading memory), ember (amber, writing memory), signal (red, destroying memory), umbra (violet, doubting memory). Color always encodes an operation, never decoration; signal red is a button background in exactly one place (the destructive-confirm control).
- Type triad: Bricolage Grotesque (display), Albert Sans (body/UI), Spline Sans Mono (machine-authored/verifiable values only — hashes, IDs, region codes).
- The memory trace: a horizontal hash-chain motif (linked dots, dashed link at an erasure, heavier anchor node at a checkpoint) — the one signature bold element; everything else stays quiet.
- Logo: an M built from three linked nodes, right leg terminating in a dashed segment ("forgetting is part of memory, not a failure of it").
- Copy rules: active voice, verb-led buttons ("Forget subject," not "Submit"), toasts state what happened and where the proof is, never soften a destructive action, say what is true and no more ("tamper-evident within one checkpoint epoch," not "tamper-proof").

## Evidence on Hand

Real and verifiable, not staged: 439 passing tests (`make check`); a published red-team suite (`docs/redteam.md`) covering 13 attacks with one disclosed successful attack (two colluding sources promote a fact — the published collusion threshold, kept honest rather than implied-perfect); three demo tenants (clinic/ops/finance) with genuinely isolated live data, confirmed by direct query (15/67/3 episodes, chain heights 709/514/329 respectively — not sampled, not seeded to look different).

**Must not be fabricated** — this is a hackathon submission, not a company with a sales history: no customer testimonials, no case studies, no pricing, no claimed production customers. Where the README or landing copy states a limitation (e.g. "Custodian's read-only guarantee is application-layer, not platform-layer" — `docs/limits.md`), that limitation is load-bearing and must not be quietly smoothed over by future design work.

## Product Principles

1. **Claim only what a test proves.** No claim ships without a named test or a measured result (`AGENTS.md`'s own rule) — this governs UI copy exactly as it governs the README.
2. **Prevention is bounded; containment is the real control.** Publish the collusion threshold and the attacks that succeed rather than implying the defense is perfect.
3. **The component that can destroy memory has no model in it, and that separation is structural.** Never let a UI implication suggest a model is closer to destructive power than IAM/CI actually allow.
4. **Every visual signal encodes real system state.** A judge should be able to read trust state or operation type from a screenshot alone, without a legend.
5. **Show the boundary, not just the win.** The demo scripts explicitly show where the defense breaks before showing the cure — the same discipline applies to what the console displays.

## Accessibility & Inclusion

`brand/BRAND.md` requirements, already implemented and to be preserved by future work: color is never the sole carrier of meaning (every trust state carries a label and a distinct shape, verified against forced-colors mode and greyscale); text contrast ratios are recorded per token and nothing under 4.5:1 is used for text; `prefers-reduced-motion` is respected globally (`brand/tokens.css`) and per-animation (the memory trace, the logo draw-in, the trust-lattice sequence, and count-up stats all freeze on a settled state rather than looping/animating when it is set).
