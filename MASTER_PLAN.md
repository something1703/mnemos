# MNEMOS — Master Build Plan

**Project:** Mnemos — Accountable Memory for Agents
**Hackathon:** CockroachDB × AWS "Build with Agentic Memory" — deadline Aug 19, 2026
**One line:** Agents don't fail when they're wrong. They fail when they forget —
or when they remember what they never should have.

## The thesis (read this before anything else)

Every team at this hackathon will build a place to put agent memory. Recall
quality is a crowded, solved-enough problem. The unsolved problem is that
**nobody can answer for what an agent remembered.**

Three questions have no good answer in any agent memory system shipping today:

1. **Where does this memory legally live?** An agent in Frankfurt recalls a
   patient's record. Did that row cross a border? Nobody knows, because
   memory layers treat storage as one undifferentiated blob.
2. **What did the agent believe at 14:32, and why did it act?** After an agent
   causes harm, you need a deposition, not a log file. Today you get neither.
3. **One poisoned fact entered memory six weeks ago. What did it touch?**
   Prompt injection that *persists* is agentic memory's defining security
   hole. A single malicious fact silently contaminates every future recall,
   forever, with unbounded blast radius — and no system can enumerate the
   damage, let alone undo it.

Mnemos answers all three, and it answers them **because it is one
transactionally-consistent distributed database and not a pile of services.**
That is the entire argument for CockroachDB, made falsifiable.

## What Mnemos is

Four planes over one CockroachDB cluster on AWS:

| Plane | Role | Contains an LLM? |
|---|---|---|
| **The Fabric** | Memory itself: episodic → semantic (C-SPANN vectors) → procedural | no |
| **The Ledger** | Accountability: sharded hash chains, Merkle attestation to S3 Object Lock, depositions | no |
| **The Warden** | Governance: residency, legal hold, quarantine, erasure, crypto-shred | **no — by law** |
| **The Custodian** | Self-maintenance: runs official CockroachDB Agent Skills over Cloud MCP, files findings back into memory | yes (Bedrock) |

The Warden is the only component that can destroy anything, and the Warden
contains no model. That is not a policy — it is an IAM boundary, a database
role, and a test.

## The three pillars (every phase serves one)

**I. RESIDENCY — memory that obeys the law of where it lives.**
`REGIONAL BY ROW` homes every episode to a jurisdiction. An agent in another
region can query the fabric and receive lawful *derived* answers, while raw
PII never leaves its home region. Data residency as an agent primitive.
No vector database on earth can do this.

**II. ACCOUNTABILITY — every action traces to the memory that caused it.**
`AS OF SYSTEM TIME` gives temporal recall: reconstruct exactly what the agent
believed at any past instant. Provenance edges bind facts to source episodes.
The ledger binds recalls to actions. `explain(action_id)` emits a signed
deposition: this action ← these facts ← these episodes ← this raw source,
hash-verified end to end and anchored outside the database.

**III. INTEGRITY — poisoned memory can be found and revoked.**
Facts carry a trust score inherited from source provenance. Low-trust facts
require independent corroboration before they become recallable. When a source
turns out to be malicious, `revoke_source(episode_id)` computes the blast
radius across facts, vectors, procedural skills, and past recalls — and
revokes all of it in one serializable transaction, then propagates the
revocation to downstream consumers via changefeed.

## Why this scores

| Rubric criterion | What answers it |
|---|---|
| Agentic Memory Design | Three tiers + provenance graph + trust lattice + temporal queries + residency, all transactional. Memory is the product, not a side table. |
| Technical Implementation | REGIONAL BY ROW, C-SPANN prefix-scoped vector index, `AS OF SYSTEM TIME`, CHANGEFEED, Row-Level TTL, RLS, DB-enforced audit trigger, sharded Merkle chains. Four of four CockroachDB tools. |
| Real-World Impact | Built against obligations in force *now*: EU AI Act Art. 12 record-keeping (high-risk regime live Aug 2026), GDPR Art. 17/44, HIPAA §164.312(b), India DPDP §12. Three demos in healthcare, security ops, and consumer finance. |
| Production Readiness | Five invariants, two enforced by the database itself; adversarial test suite; a dedicated red-team phase that attacks our own memory; WORM-anchored proofs; crypto-shredding that survives backups. |
| Creativity & Originality | Nobody is building governed memory. The Warden/Custodian split, blast-radius revocation, and deposition-grade temporal recall have no prior art in this space. |

## Phase index
| # | Phase | Produces |
|---|-------|----------|
| 01 | Foundations & Brand | Repo, CI, accounts, locked brand system, logo |
| 02 | Database Core | Multi-region schema, RLS, TTL, KMS envelopes, chains, changefeeds |
| 03 | Memory Engine | Core library: remember / recall / recall_as_of / forget / revoke_source |
| 04 | Mnemos MCP API | FastMCP on Lambda + API Gateway, scoped keys, tool surface |
| 05 | Sleep Cycle | Bedrock consolidation, belief revision, trust propagation, decay |
| 06 | Governance Plane (the Warden) | Residency, legal hold, quarantine, erasure modes, attestation, depositions |
| 07 | Custodian | Fargate agent, Cloud MCP, official Agent Skills, ops self-knowledge |
| 08 | Console UI | Explorer, Ledger, Residency map, Deposition viewer, Blast radius, Forget |
| 09 | Demo Verticals | Continuity / Contagion / Deposition — one per pillar |
| 10 | Adversarial Phase | Red team: poison, exfiltrate, tamper, race, injure. Publish results. |
| 11 | Hardening & Contribution | Load, resilience, observability, 2 upstream skill PRs |
| 12 | Submission | Video, README, Devpost, judge simulation |

## How to use this plan
Execute phases in order. Each file is self-contained: objective, required
inputs, sub-phases with acceptance criteria, Definition of Done. Do not start
a phase until the previous phase's DoD is fully checked. Where a phase lists
**Inputs needed from the user**, the executing agent MUST ask and wait —
never fabricate credentials, connection strings, or cluster IDs.

Quality bar: this is not a hackathon prototype with a demo path. Every
acceptance criterion is a test that lives in CI. If a feature cannot be
proven by a test or filmed as a proof, it does not ship.

## Global conventions
- **Repo:** monorepo `mnemos/` — `packages/engine`, `packages/warden`,
  `services/api`, `services/sleep-cycle`, `services/custodian`,
  `apps/console`, `demos/`, `redteam/`, `infra/`, `db/`, `brand/`, `docs/`.
- **License:** Apache-2.0, present from the first commit, visible in About.
- **Language:** Python 3.12 (engine/services), TypeScript (console), SQL (db).
- **IaC:** everything deployable from `infra/` — no undocumented click-ops.
- **Secrets:** never in code. Local `.env` (gitignored) + AWS Secrets Manager.
  `.env.example` always current. gitleaks in CI and before every push.
- **Testing gate:** code lands with tests; CI green closes a sub-phase.
  Coverage floor 90% on `packages/engine` and `packages/warden`.
- **Every transaction** goes through the 40001 retry wrapper. No exceptions.

## The five sacred invariants
Violating any of these fails the project. Two are enforced by CockroachDB
itself, not by convention.

1. **No LLM-driven process ever holds DELETE or governance privileges.**
   The Warden destroys; the Warden has no model. Enforced by DB role grants.
2. **Every state-changing memory op appends a hash-chained audit row in the
   same transaction.** Enforced by a database trigger that aborts the
   mutation if the audit row is absent.
3. **No fact becomes recallable without provenance to at least one episode.**
   No orphan beliefs, ever.
4. **Memory rows never leave their home region.** Only derived, policy-
   approved projections cross a jurisdictional boundary, and every crossing
   is logged.
5. **Erasure is atomic across rows, vectors, and provenance — or it does not
   happen.** Legal hold outranks erasure and says so explicitly.

## Credentials the user will be asked for (summary, by phase)
- Phase 01: GitHub org; CockroachDB Cloud account; AWS account
- Phase 02: CockroachDB Cloud connection string; confirm v25.2+ (C-SPANN)
- Phase 04: AWS profile with IAM rights; chosen home region
- Phase 05: Bedrock model access (Claude + Titan Embed V2); cost guardrail
- Phase 06: KMS key policy approval; S3 Object Lock bucket approval (WORM —
  objects genuinely cannot be deleted for the retention period; user must
  understand and approve this before creation)
- Phase 07: CockroachDB Cloud service account + MCP cluster ID
- Phase 12: Devpost account; YouTube/Vimeo access
