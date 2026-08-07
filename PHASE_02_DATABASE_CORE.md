# Phase 02 — Database Core

**Objective:** The Mnemos schema live on CockroachDB — multi-region-shaped,
row-level-secure, envelope-encrypted, with sharded hash chains and
changefeeds. This schema is the product. Everything above it is a wrapper.

## Inputs needed from the user — ASK BEFORE STARTING
1. CockroachDB Cloud connection string for a newly created cluster (user
   creates it in the console; agent walks them through if needed).
2. Confirmation of cluster version — need **v25.2+** for C-SPANN vector
   indexes. Record the exact version.
3. Approval to create an AWS KMS customer-managed key (CMK) per tenant for
   envelope encryption (cost: ~$1/key/month — confirm the budget).

## Sub-phase 2.1 — Cluster, connectivity, and the local multi-region rig
- [ ] Cloud cluster created, SQL user provisioned, connection string in local
      `.env` as `MNEMOS_DB_URL` (`sslmode=verify-full`), never committed.
- [ ] `db/scripts/ping.py` — connects, prints version, feature-probes for
      `VECTOR`, `TSVECTOR`, `REGIONAL BY ROW`, RLS, triggers, changefeeds.
      Output committed as `docs/cluster-capabilities.md`.
- [ ] **`make db-local`** — single-node Docker CockroachDB for fast tests.
- [ ] **`make db-multiregion`** — a **9-node local cluster** (3 nodes × 3
      simulated localities: `region=us-east-1`, `region=eu-central-1`,
      `region=ap-south-1`) via docker-compose. This rig is how residency and
      node-kill resilience get demonstrated; the free-tier cloud cluster
      cannot show either. Script it once, use it in Phases 06, 09, 10, 11.
**Accept:** ping works against cloud, local single-node, and the 9-node rig;
`SHOW REGIONS` on the rig lists three regions.

## Sub-phase 2.2 — Migrations
Adopt a migration runner (dbmate or alembic with raw SQL). Ordered migrations:

| # | Migration | Contents |
|---|---|---|
| 001 | tenancy | `tenants`, `agents`, `api_keys`, region policy per tenant |
| 002 | episodic | `episodic_events` — Row-Level TTL, `REGIONAL BY ROW`, KMS-wrapped content |
| 003 | semantic | `semantic_facts` (+ `embedding VECTOR(1024)`, `tsv TSVECTOR`, trust state, strength, confidence, `superseded_by`) |
| 004 | provenance | `fact_provenance` edges (fact ← episode), `recall_log`, `action_log` — the accountability graph |
| 005 | procedural | `skills`, `skill_versions`, `skill_outcomes` |
| 006 | governance | `legal_holds`, `residency_policies`, `region_crossings`, `revocations` |
| 007 | ledger | sharded `audit_chain` (per tenant × shard), `chain_checkpoints` (Merkle roots) |
| 008 | custodian | `custodian_runs`, `custodian_findings` |
| 009 | rls | Row-Level Security policies on every tenant-scoped table |
| 010 | triggers | the audit-enforcement trigger (invariant 2) and the provenance-enforcement constraint (invariant 3) |
| 011 | changefeeds | CDC on `semantic_facts` and `revocations` |

- [ ] **Day-one syntax verification:** run VECTOR INDEX, TSVECTOR,
      `REGIONAL BY ROW`, RLS policy, and trigger DDL against the actual
      cluster version *before* writing application code. Record every
      dialect surprise in ADR-006. Do not let a syntax assumption survive
      to Phase 03.
- [ ] Vector index is **prefix-scoped by `tenant_id`** so tenant isolation
      lives inside the index itself, not just in the WHERE clause.
- [ ] TTL smoke test: insert a row with `expire_at` in the past, confirm the
      TTL job removes it and that the removal does not orphan derived facts
      (it must not — provenance uses `ON DELETE` semantics we choose
      deliberately and document in ADR-007).
**Accept:** `make db-migrate` idempotent from zero on cloud, local, and rig.

## Sub-phase 2.3 — Multi-region residency (pillar I foundation)
- [ ] `episodic_events` and `semantic_facts` are `REGIONAL BY ROW` with a
      `crdb_region` derived from the tenant's residency policy for that
      subject — not from where the writer happens to be.
- [ ] Survival goal set and documented (`SURVIVE REGION FAILURE` on the rig;
      note the free-tier cloud cluster is single-region and cannot).
- [ ] `db/scripts/where_is.py <subject_key>` — prints the physical region of
      every row for a subject, read from `crdb_region` + range locality. This
      script is a video moment: *point at a patient record and show which
      country it is sitting in.*
- [ ] Locality-optimized read test: a session pinned to `eu-central-1` reads
      an EU-homed subject without leaving the region; the same query for a
      US-homed subject is correctly refused by policy, not silently served.
**Accept:** `where_is.py` returns the expected region for seeded subjects in
all three localities; region-crossing attempt is denied and logged.

## Sub-phase 2.4 — Envelope encryption + crypto-shred readiness
- [ ] Per-tenant AWS KMS CMK. Episode content and fact text are stored
      encrypted with a data key wrapped by the tenant CMK (envelope pattern);
      the wrapped DEK lives in the row, plaintext DEK never persists.
- [ ] Embeddings are computed pre-encryption and stored as vectors (they must
      be searchable) — **document this honestly**: embeddings are a lossy but
      non-zero information leak of the source text, which is exactly why
      erasure must delete vectors transactionally and why crypto-shred alone
      is insufficient. This paragraph goes in `docs/security.md`.
- [ ] Crypto-shred path proven at the schema level: destroying a tenant's CMK
      renders all ciphertext columns permanently unreadable, *including copies
      inside backups and MVCC history*. This is the honest answer to "but the
      backup still has it." Full flow implemented in Phase 06.
**Accept:** round-trip encrypt/decrypt test; a test that revokes access to a
test CMK and proves the rows become undecryptable.

## Sub-phase 2.5 — The ledger: sharded chains + Merkle checkpoints
A naive per-tenant `max(seq) FOR UPDATE` serializes every write in the tenant
and becomes the system's throughput ceiling on a distributed database. We
solve it rather than confess it.

- [ ] `audit_chain` is keyed `(tenant_id, shard_id, seq)` with N shards
      (default 16, configurable). Each mutation hashes into exactly one shard
      chosen by hashing the subject key — so a subject's history is a single
      ordered chain, while tenant-wide throughput scales with N.
- [ ] `chain_checkpoints`: every epoch (default 1h, and on demand), compute a
      **Merkle root over all shard heads** for a tenant and commit it as a
      checkpoint row. The checkpoint binds the shards into one verifiable
      state, restoring a single root of trust.
- [ ] Canonical serialization spec for hashing: sorted-key JSON, explicit
      UTF-8, explicit numeric formatting, documented byte-for-byte in
      `docs/ledger.md` so the verifier is reimplementable in any language.
      A judge should be able to verify our chain with a Python one-liner.
- [ ] The **audit-enforcement trigger** (invariant 2): a `BEFORE` trigger (or
      equivalent constraint mechanism verified in 2.2) on every protected
      table that aborts the statement unless a matching audit row is written
      in the same transaction. If the cluster version cannot express this as
      a trigger, implement it via a mandatory stored procedure interface and
      revoke direct DML from all application roles — record the choice in
      ADR-008. Either way, **the invariant is enforced by the database.**
**Accept:** contention benchmark shows sharded chains scale ~linearly with
shard count up to 16; a direct `INSERT` into a protected table without an
audit row is rejected by the database, from a superuser session.

## Sub-phase 2.6 — RLS verification (adversarial)
- [ ] Test: a connection with `app.tenant_id = A` cannot SELECT/UPDATE/DELETE
      tenant B rows across all protected tables — including via crafted SQL,
      subqueries, `RETURNING`, CTEs, and vector similarity search (a vector
      query must not leak neighbors across tenants).
- [ ] Test: TTL background deletion still works across tenants despite RLS.
- [ ] Service roles defined and granted minimally:
      `mnemos_api` (RLS-bound, no DELETE),
      `mnemos_pipeline` (BYPASSRLS for consolidation, no DELETE),
      `mnemos_warden` (**the only role with DELETE**, no Bedrock access in its
      execution environment),
      `mnemos_readonly` (Custodian fallback if direct SQL is ever needed —
      default is MCP-only).
- [ ] Test proving `mnemos_api` and `mnemos_pipeline` are *denied* DELETE by
      the database (invariant 1, enforced not promised).
**Accept:** adversarial suite green; role/grant matrix in `docs/security.md`.

## Sub-phase 2.7 — Changefeeds
- [ ] CHANGEFEED on `semantic_facts` → console live updates (via the API's
      SSE endpoint in Phase 08).
- [ ] CHANGEFEED on `revocations` → the revocation bus. When a source is
      revoked in Phase 06, downstream consumers (caches, the console, any
      external subscriber) learn within seconds. This is how a revocation
      escapes the database and reaches things that already read the bad fact.
**Accept:** a test subscriber receives a fact insert and a revocation event.

## Sub-phase 2.8 — Seed & fixtures
- [ ] `db/seed.py`: 3 tenants across 3 residency policies, 5 agents, ~500
      episodic events over 8 sessions with realistic domain content
      (clinical, ops, financial), pre-made facts with real Titan embeddings
      (deterministic fake vectors for CI), one deliberately poisoned source
      episode for Phase 10, one subject under legal hold.
- [ ] Fixture factory usable by engine tests (Phase 03).
**Accept:** seeded DB supports a manual hybrid-search query returning sane
results, and `where_is.py` shows subjects in all three regions.

## Sub-phase 2.9 — Schema validated by the official Agent Skill
- [ ] Run the `cockroachdb-sql` skill's anti-pattern checklist against our
      schema (sequential IDs, missing PKs, wrong types, hotspots, unbounded
      ranges). Fix everything it flags. Record the pass in ADR-009 — this
      line goes in the README, because "we used the sponsor's own skill to
      audit our schema" is a strong technical-implementation signal.

## Definition of Done
- [ ] Migrations idempotent on cloud + local + 9-node rig.
- [ ] TTL, RLS, C-SPANN, REGIONAL BY ROW, triggers, changefeeds all proven by
      tests, not assumption.
- [ ] Sharded chain + Merkle checkpoint benchmark published.
- [ ] Invariants 1, 2, and 3 enforced at the database layer and each has a
      test that tries to violate it and fails.
**Est: 4–5 days. This phase carries the whole build — do not rush 2.5 or 2.6.**
