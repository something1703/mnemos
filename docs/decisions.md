# Architecture Decision Records

One entry per decision that a future contributor would otherwise have to
reverse-engineer. AGENTS.md requires an ADR for any deviation from the phase
files, approved before the deviation is made.

Format: context → decision → consequences, including the ones we dislike.

---

## ADR-001 — Monorepo

**Context.** Mnemos is one system with five deployable units (engine, warden,
api, sleep-cycle, custodian) plus a console and demos. They share a schema, a
canonical hash serialization, and a set of invariants that must be tested
together.

**Decision.** A single repository with a uv workspace for Python and a pnpm
workspace for the console.

**Consequences.** Cross-cutting invariant tests are trivial to write and
impossible to skip. A judge clones one thing. The cost is a heavier CI matrix
and no independent versioning — acceptable, since nothing here ships
separately.

---

## ADR-002 — Four planes, and the Warden holds no model

**Context.** The single most dangerous property of an agentic memory system is
that a language model, which can be manipulated by its own input, is trusted to
decide what gets destroyed. Prompt injection then escalates from "wrong answer"
to "irreversible data loss."

**Decision.** Split the system into four planes — Fabric (memory), Ledger
(accountability), Warden (governance), Custodian (self-maintenance) — and give
destructive authority exclusively to the Warden, which contains no model call.
The Custodian, which *is* an LLM agent, may propose governance actions but never
execute them.

**Consequences.** An extra service and an IAM hop on every destructive
operation. In exchange, invariant 1 becomes structural: it is enforced by role
grants, an IAM deny on `bedrock:InvokeModel`, a CI check on the Warden's
transitive import graph, and a runtime self-check. A reviewer can verify it by
reading a policy document rather than auditing prompts.

---

## ADR-003 — The audit ledger lives in the same database as the memory

**Context.** An audit log in a separate system is only as trustworthy as the
weakest consistency guarantee between the two. If memory commits and the audit
row does not, the record is a lie in the direction that matters most.

**Decision.** The ledger is a table in the same CockroachDB cluster, written in
the same serializable transaction as the mutation it describes.

**Consequences.** This is the reason Mnemos is a CockroachDB project rather
than a Postgres-plus-vector-store project: vectors, provenance, and audit share
one transaction boundary, which is what makes atomic erasure and blast-radius
revocation expressible at all. The cost is that a database administrator could
in principle rewrite both together — addressed by ADR-010.

---

## ADR-004 — Brand locked after Phase 01

**Context.** Visual improvisation late in a build produces an incoherent
console, and the console is what the video films.

**Decision.** Four semantic accents locked in `brand/tokens.css`: synapse teal
= recall, ember amber = consolidation, signal red = destruction, umbra violet =
doubt. Three typefaces. No new colors or fonts after Phase 01.

**Consequences.** Color carries information rather than decoration — a viewer
can read trust state from a screenshot. The umbra token exists specifically so
that an unverified fact is visually distinct from a believed one, which is the
trust lattice's whole legibility story. Constraining, deliberately.

---

## ADR-006 — Cluster capabilities verified by execution, not by documentation

**Context.** The plan depends on capabilities that drift across CockroachDB
releases and differ across Cloud tiers: C-SPANN vector indexes, triggers, RLS,
`AS OF SYSTEM TIME`, changefeeds, and zone configuration. Assuming any of them
and discovering the truth in Phase 06 would be expensive.

**Decision.** `db/scripts/probe.py` executes real DDL for each capability in a
scratch schema and writes `docs/cluster-capabilities.md`. Every probe declares
its criticality, what depends on it, and the fallback if absent — the fallback
written *before* the answer was known, so that every dependency has a
documented escape hatch.

**Result (2026-08-07).**

| Cluster | Version | Result |
|---|---|---|
| CockroachDB Cloud Basic (`mnemos`, aws-us-east-1) | v26.2.5 | **13 / 13 supported** |
| Local Docker single-node | v26.2.5 | 13 / 13 supported |

Notably available, and all previously uncertain:

- **`VECTOR INDEX` (C-SPANN)** — available on Basic, enabled by default on
  Cloud. Locally it requires `SET CLUSTER SETTING feature.vector_index.enabled`.
- **Triggers** — available. Invariant 2 therefore gets true database-level
  enforcement, and the ADR-008 stored-procedure fallback is **not needed**.
- **Row-Level Security** — available, so the Phase 10.2 test ("disable the
  middleware and prove the database alone still holds") is achievable.
- **Zone configuration on user tables** — available on Basic, which is what
  makes extended retention under legal hold possible (Phase 06.3).
- **Changefeeds** — available; enabled by default on Cloud, requires
  `kv.rangefeed.enabled` locally.

**Consequences.** The plan runs as written. `make db-local` now enables the two
settings that Cloud enables for us, so local and cloud behave identically.

---

## ADR-007 — Local Docker version is pinned to the Cloud cluster version

**Context.** The first local rig ran v25.3.0 while the Cloud cluster runs
v26.2.5. A capability that passes locally and fails in the cloud would surface
during the demo, which is the worst possible moment.

**Decision.** `db/docker/single-node.yml` and `multi-region.yml` pin the exact
Cloud version. Bump them together, never separately.

**Consequences.** One more thing to remember on a Cloud auto-upgrade. Cheap
insurance; the probe will catch a mismatch because it prints the version it
actually connected to.

---

## ADR-008 — (superseded) Stored-procedure fallback for invariant 2

**Superseded by ADR-006.** This ADR reserved a fallback for enforcing the
audit-row invariant if triggers were unavailable: revoke direct DML from every
application role and route all writes through stored procedures. The probe
confirmed triggers are available on both target clusters, so the trigger path
in migration 010 is the implementation. Retained here because the fallback
remains the correct answer if a future cluster version removes trigger support.

---

## ADR-009 — Schema reviewed by the official `cockroachdb-sql` Agent Skill

**Status:** pending — closes with Phase 02.9.

The schema is audited against the sponsor's own anti-pattern checklist
(sequential IDs, missing primary keys, wrong types, hotspots, unbounded ranges)
and the findings fixed before migrations are frozen. Recorded here because
"we used the vendor's skill to review our own schema" is both good practice and
direct evidence for the Technical Implementation criterion.

---

## ADR-010 — Proofs are anchored outside the database

**Context.** ADR-003 puts the ledger in the same cluster as the data it
describes. That is correct for atomicity and wrong for adversarial durability:
an administrator with write access could rewrite a shard's rows *and* recompute
its internal hashes, producing an internally consistent forgery.

**Decision.** Every checkpoint's Merkle root is written to an S3 bucket with
Object Lock in compliance mode, where it cannot be altered or deleted by anyone
— including the account root — until retention expires. `mnemos-attest` verifies
the live database against the anchored root.

**Consequences.** Tamper detection becomes real rather than nominal, but it is
bounded: an attacker's window is up to one checkpoint epoch. We publish the
epoch and the analysis in `docs/limits.md` rather than claiming
tamper-proofness. Object Lock also means we cannot clean up demo anchors early
— accepted, with a 30-day retention.

---

## ADR-011 — The Custodian uses Cloud MCP tools, not the skills' raw SQL

**Context.** Phase 07.1 assumed the Custodian would extract diagnostic SQL from
the official Agent Skills' `references/` files into an allowlist and execute it.
The Phase 02.1 probe found that **`crdb_internal` and `system` are restricted on
CockroachDB Cloud Basic** (`Access to crdb_internal and system is restricted`).
The ops-oriented skills lean heavily on `crdb_internal` tables, so much of their
raw SQL cannot run on our cluster at all.

**Decision.** The Custodian treats the Cloud MCP server's purpose-built
tools — `show_running_queries`, `show_statement`, `explain_query`,
`get_table_schema`, `list_tables` — as its allowlist, and uses each skill's
`SKILL.md` triage guidance as the interpretation prompt. The skills supply the
expertise; the MCP server supplies the safe accessors.

**Consequences.** Arguably a better design than the original: the allowlist
becomes a fixed set of vendor-maintained tools rather than SQL strings we parsed
out of markdown, which removes a whole class of injection surface. It also means
the Custodian works identically on Basic, Standard, and Advanced. The cost is
that a few skill diagnostics have no MCP equivalent; those are skipped and
logged as unavailable rather than silently omitted. Phase 07.1 is updated
accordingly, and this becomes genuine feedback for the sponsor's optional
feedback field.

---

## ADR-012 — Development MCP access is write-capable; the Custodian's will not be

**Context.** The `cockroachdb-cloud` MCP server was authorized via OAuth in
**write** mode for development. Its exposed tool set includes `create_database`,
`create_table`, and `insert_rows`. This is a working session for a human plus an
assistant, not a component of Mnemos — but it deserves recording, because an
LLM currently holds write access to the cluster, and that is precisely the
arrangement the whole project argues against.

**Decision.** Two separate credentials, never conflated:

1. **Development (this OAuth session).** Write-capable. Used read-only in
   practice: schema changes go through versioned migrations in `db/migrations`,
   never through `create_table` over MCP, because reproducibility from a clean
   clone is a Phase 12 requirement.
2. **The Custodian (Phase 07).** A dedicated CockroachDB Cloud service account,
   read-only, with a startup probe that enumerates its own tools and hard-fails
   if any write-capable tool is reachable.

**Consequences.** The invariant is about the deployed system, and it holds
there. Being explicit about the dev-time exception is more honest than quietly
relying on the reader assuming otherwise — and the Phase 07 startup probe is
what turns the claim into a test.

---

## ADR-013 — Real KMS keys and a real S3 Object Lock bucket, provisioned 2026-08-08

**Context.** Phase 06.4 (crypto-shred) and 06.6 (ledger attestation) were
built against interfaces only — `KmsKeyProvider` and the S3 anchoring path
were `NotImplementedError` stubs, pending the explicit user approval AGENTS.md
requires before creating a KMS key policy or a WORM bucket. The user approved
both, with specific parameters: one CMK per demo tenant, and 7-day (not
30-day) Object Lock retention.

**Decision.**

- **Three KMS CMKs**, one per demo tenant (`alias/mnemos-clinic`,
  `alias/mnemos-ops`, `alias/mnemos-finance`), `us-east-1`, automatic annual
  rotation enabled. Key policy grants the `mnemos` IAM user
  `Encrypt`/`Decrypt`/`GenerateDataKey`/`DescribeKey` and, directly,
  `ScheduleKeyDeletion`/`CancelKeyDeletion` — because no separate deployed
  Warden execution role exists yet (that narrowing is Phase 04 deployment
  infra's job; today, in dev, the Warden's process IS this IAM user).
- **One S3 bucket** (`mnemos-ledger-anchor-582054875648`), Object Lock enabled
  at creation (required — cannot be added after), default retention
  **COMPLIANCE mode, 7 days**, versioning auto-enabled by Object Lock, all
  public access blocked, SSE-S3 default encryption.
- **Public access is blocked**, which deviates from PHASE_06.6's original
  "public-readable for demo tenants" sketch. A private bucket is the safer
  default — public S3 buckets are a leading real-world incident source, and a
  Merkle root gains nothing from being world-readable by default.
  `presign_anchor_url()` (`mnemos_warden.attestation`) generates a
  time-limited link for sharing one specific anchor with a judge, without
  granting them standing AWS credentials.

**Verified, not assumed — WORM behaviour tested empirically before writing
any code against it:**

- A bare `DeleteObject` on a locked key does **not** error. S3 versioning
  makes it create a *delete marker* — a new "current version" that hides the
  object — while the actual locked version is untouched underneath.
- `DeleteObject` targeting the **specific locked version ID** returns
  `AccessDenied: Access Denied because object protected by object lock` —
  even with `--bypass-governance-retention`, which only ever applies to
  GOVERNANCE mode. COMPLIANCE mode has no bypass, for any principal,
  including account root.
- Removing the delete marker (itself unlocked) restores visibility of the
  original object, unchanged.

This distinction matters for `verify_against_anchor`: it always reads by
`(tenant_id, checkpoint_seq)` key, which resolves to the current version. A
delete-marker attack (hide, don't destroy) would make an anchor look
*missing* rather than *wrong* — a different failure mode than tampering, and
one `mnemos-attest verify` should be read as covering ("the anchor could not
be found" vs. "the anchor disagrees with the live chain").

**The claim this closes, proven end-to-end against real infrastructure
(`tests/warden/test_attestation.py`, `make test-aws`):** an attacker with
full database DML rights can rewrite a shard's entries **and**
`chain_checkpoints.merkle_root` consistently — an internally-consistent
forgery that fools `mnemos-verify`'s in-database check completely (proven:
`verify_chain` reports `valid=True` against the forged state). The same
forgery is caught by `mnemos-attest verify`, because the root committed to S3
before the rewrite is outside that attacker's reach. This is the concrete
difference between "tamper-evident within one checkpoint epoch" and
"tamper-evident if nobody with database access is the adversary" — and it is
why `docs/ledger.md` §5.3 exists.

**Consequences.** `docs/limits.md`'s "tamper-evident, not tamper-proof"
section is updated to state the anchored guarantee precisely rather than
conditionally. The 7-day retention means these specific KMS keys and this
specific bucket's contents survive at minimum through 2026-08-15 — safely
past both the hackathon deadline and the judging window — and the bucket
cannot be deleted or emptied before then even if the project is torn down
early.

---

## ADR-014 — Model calls run on OpenAI, not Amazon Bedrock

**Context.** The original plan called for Bedrock (Claude for distillation
and contradiction judging, Titan Embed v2 for embeddings), and early
documentation — including this project's own README at points during the
build — said so. `docs/accounts.md`'s "still to provision" checklist has
carried an unchecked "Bedrock model access" item since account setup:
Bedrock model access requires an AWS account-level approval step that adds
real latency, and the interpretation layer (`mnemos_engine.llm`) was
deliberately built provider-agnostic from Phase 03 specifically so a stalled
approval wouldn't block the rest of the build. It stalled; the substitution
was used, and — this is the part worth stating plainly — several docs kept
saying "Bedrock" anyway, including two files written during a later design
pass on this same repo that repeated the same stale claim without checking
`/health` first.

**Decision.** Ship on OpenAI (`gpt-5.6-luna` for distillation and Custodian
interpretation, `text-embedding-3-small` for embeddings), reachable and
confirmed at `GET /health`'s `posture.model_provider`. Every doc making an
AWS-service claim about model calls is corrected to say so, rather than
left to be discovered by a judge who actually checks the endpoint the
README itself tells them to check.

**Consequences.** The hackathon's AWS-services requirement is still met —
comfortably — on Lambda, Step Functions, ECS Fargate, S3 + Object Lock,
KMS, EventBridge, CloudWatch, API Gateway, and Secrets Manager, none of
which this ADR touches. What it costs is one AWS-services rubric line this
project cannot honestly claim. The trade against silently keeping the
Bedrock claim was never close: a claim a judge can falsify with one `curl`
is worse than the true version of the same sentence. `mnemos_engine.llm`
being provider-agnostic by design (Phase 03, not a retrofit) is also why
this substitution cost nothing structurally — swapping the provider back,
if Bedrock access clears, is a config change, not a rewrite.
