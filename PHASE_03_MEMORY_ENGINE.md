# Phase 03 — Memory Engine (core library)

**Objective:** `packages/engine` — the pure-Python heart of the Fabric. Every
memory read and write lives here, fully tested against local CockroachDB. No
AWS, no HTTP, and **no destructive operations** (those live in the Warden,
Phase 06). Later phases are thin wrappers around this package.

## Inputs needed from the user
None (runs against local/cloud DB from Phase 02).

## Sub-phase 3.1 — Package skeleton & data layer
- [ ] `mnemos_engine` package: async psycopg3 (matching langchain-cockroachdb's
      approach for checkpointer compatibility) with SQLAlchemy Core for
      composition.
- [ ] Typed pydantic models: `Episode`, `Fact`, `ProvenanceEdge`, `Skill`,
      `RecallRecord`, `ActionRecord`, `Finding`, `AuditRow`, `Checkpoint`,
      `TrustState`, `ResidencyPolicy`.
- [ ] Connection manager: pooling, per-request `SET app.tenant_id`, optional
      locality pinning for residency tests, and the **mandatory** retry
      wrapper on serialization errors (40001) with bounded exponential
      backoff and jitter. Every transaction goes through it; a lint rule or
      unit test forbids raw `conn.transaction()` outside the wrapper.
- [ ] Structured logging with `run_id` / `session_id` / `trace_id`
      correlation from the first line of code, not retrofitted in Phase 11.
**Accept:** 40001 retry proven by a test that forces genuine contention; a
test asserts no code path bypasses the wrapper.

## Sub-phase 3.2 — remember()
- [ ] `remember(tenant, agent, session, subject_key, event_type, content,
      source_trust, s3_artifact=None, idempotency_key=None)` → one
      serializable txn: encrypt content (KMS envelope) → insert episodic row
      homed to the subject's residency region → append audit row.
- [ ] **`source_trust`** is required, not optional: `system` (deterministic
      internal), `operator` (authenticated human), `agent` (LLM-generated),
      `external` (third-party / user-supplied / tool output). This single
      field is what makes Phase 10's poisoning defense possible. Default is
      the *least* trusted value the caller's key scope allows.
- [ ] Idempotency: duplicate key returns the original `event_id`, no new rows,
      no new audit row.
- [ ] Residency enforcement: writing a subject whose policy homes it to
      another region either routes correctly or refuses — never silently
      writes locally. The refusal path logs a `region_crossing` denial.
- [ ] Property test: N concurrent `remember` calls with the same idempotency
      key → exactly one row and chain length +1.
**Accept:** p50 write < 50ms local; concurrency and residency tests green.

## Sub-phase 3.3 — recall()
- [ ] `recall(tenant, query, subject_key=None, k=8, include_unverified=False,
      as_of=None)` → parallel retrievals:
      (a) hybrid semantic — C-SPANN vector + TSVECTOR full-text fused with
          RRF (via langchain-cockroachdb `HybridSearchConfig`),
      (b) episodic tail for the active session,
      (c) procedural match on task embedding.
- [ ] **Trust gate:** facts in `unverified` or `quarantined` state are
      excluded by default and never silently included. When
      `include_unverified=True`, results are tagged so the caller (and the
      console, in umbra violet) can see they are drinking unfiltered water.
- [ ] Ranking: `score = similarity × ln(1 + strength) × confidence × trust_weight`.
      Every component returned in the result so scoring is inspectable, not
      magic. The console renders the breakdown on hover.
- [ ] **Contested results:** when the top-k contains facts with a supersession
      or contradiction relationship, `recall` returns a `contested` block
      naming both sides with their evidence rather than silently picking a
      winner. An agent that knows it is unsure is more useful than one that
      guesses. This is a first-class return field, not a warning.
- [ ] Reinforcement side-effect in the same txn: `recall_count++`,
      `last_recalled_at = now()`, audit row `op='reinforce'`.
- [ ] **`recall_log` write (pillar II):** every recall records which fact IDs
      were returned to which agent in which session, at what score. This table
      is what makes depositions possible later. It is append-only and it is
      the reason `explain()` can work at all.
- [ ] Embedding provider is an interface: `TitanEmbedder` (Bedrock, wired in
      Phase 05) + `FakeEmbedder` (deterministic, for CI).
**Accept:** relevance test on seeded data (known query → expected fact in
top-3); reinforcement demonstrably reorders results across repeated recalls;
a quarantined fact is provably absent from default results.

## Sub-phase 3.4 — recall_as_of() — temporal recall (pillar II)
The feature no other memory layer has, because no other memory layer sits on
MVCC with `AS OF SYSTEM TIME`.

- [ ] `recall_as_of(tenant, query, timestamp, ...)` runs the entire hybrid
      retrieval `AS OF SYSTEM TIME <ts>` — reconstructing exactly what the
      agent would have recalled at that instant: the facts as they were, the
      strengths as they were, before any later supersession or revocation.
- [ ] **Bounded honestly:** the window is limited by `gc.ttlseconds`. The
      engine reads the cluster's actual GC config, exposes
      `temporal_window()`, and raises a typed
      `OutsideTemporalWindow` error with the real boundary rather than
      returning a wrong answer. For subjects under legal hold or flagged for
      audit, Phase 06 extends the GC TTL on those ranges so the window covers
      the retention obligation — deliberate, documented, tested.
- [ ] Test: write facts, supersede one, revoke another, then prove
      `recall_as_of(t0)` returns the *original* world and `recall(now)`
      returns the current one.
- [ ] Test: `recall_as_of` outside the GC window fails loudly with the
      boundary in the error, and never silently degrades to `now()`.
**Accept:** the three tests above green; `temporal_window()` matches the
cluster's real `gc.ttlseconds`.

## Sub-phase 3.5 — The provenance & accountability graph (pillar II)
- [ ] `record_action(tenant, agent, session, action_type, description,
      recall_ids[])` — the agent declares "I did X because of these recalls."
      Written in the same txn as an audit row.
- [ ] `explain(action_id)` → a **deposition object**: action → recalls →
      facts (with the values they held *at recall time*, via 3.4) → provenance
      edges → source episodes → raw content hashes → the audit rows for every
      one of those events → the enclosing Merkle checkpoint. Every node
      carries its hash; the whole structure is independently verifiable.
- [ ] Deposition serializer: deterministic JSON + a human-readable rendering.
      A regulator with a hash function and no Mnemos install must be able to
      verify it. `docs/deposition.md` specifies the format.
- [ ] Test: build an action from real recalls, mutate an upstream fact, and
      prove the deposition still reports the historical value *and* flags
      that the fact has since changed.
**Accept:** a deposition for a seeded action verifies end-to-end against the
ledger with an independent verifier script that imports nothing from
`mnemos_engine`.

## Sub-phase 3.6 — Trust lattice & blast radius (pillar III, read side)
The Warden performs revocation (Phase 06); the engine computes and exposes it.

- [ ] Trust states: `unverified` → `corroborated` → `trusted`, plus
      `contested` and `quarantined`. State transitions are explicit,
      audited, and only ever triggered by the sleep cycle's corroboration
      gate (Phase 05) or the Warden.
- [ ] `blast_radius(episode_id | source)` → the full transitive closure:
      facts derived from it, other facts whose corroboration depended on
      those facts, procedural skills that cite them, every recall that
      returned them, and every action declared on those recalls. Returned as
      a graph with counts per hop.
- [ ] Performance: blast radius over a 100k-fact tenant returns in < 2s.
      Recursive CTE, indexed provenance edges, measured and published.
- [ ] Test: seed a poisoned episode with second-order derivations and prove
      the closure catches the *indirect* descendants, not just direct ones.
      Catching second-order contamination is the whole point.
**Accept:** the second-order test green; the perf number recorded in
`docs/scale.md`.

## Sub-phase 3.7 — Procedural memory
- [ ] `learn_skill(tenant, name, playbook, task_description, source_trust)` —
      versioned insert. **Skills learned from `agent` or `external` trust land
      quarantined** until corroborated; an agent must never be able to teach
      itself an unvetted procedure and execute it. This is the single most
      dangerous path in any agentic memory system and we close it by default.
- [ ] `find_skill(task)` by task-embedding similarity, trust-gated.
- [ ] `record_outcome(skill_version, success, latency)` updates fitness
      counters; repeated failure demotes trust automatically.
**Accept:** round-trip test (learn → find via semantic paraphrase → outcome);
a test proving an `agent`-trust skill is not returned by `find_skill` until
corroborated.

## Sub-phase 3.8 — The ledger client & verifier
- [ ] `append_audit(txn, ...)`: selects the shard from the subject key, reads
      that shard's head, computes `sha256(canonical_payload || prev_hash)`,
      inserts. Genesis row rule defined for each shard.
- [ ] `checkpoint(tenant)`: computes the Merkle root over all shard heads,
      commits a `chain_checkpoints` row. (S3 WORM anchoring lands in Phase 06.)
- [ ] **`mnemos-verify` CLI**: walks every shard, recomputes every hash,
      recomputes each Merkle root, and reports VALID or the exact first broken
      link with its shard, seq, expected hash, and found hash.
- [ ] Tamper tests: (a) a manual UPDATE on a ledger row is caught;
      (b) a deleted middle row is caught; (c) a whole rewritten shard is
      caught *by the checkpoint* even though that shard is internally
      consistent. Test (c) is the one that proves checkpoints earn their keep.
**Accept:** all three tamper tests green; verifier over 100k rows < 10s.

## Definition of Done
- [ ] ≥90% coverage on `packages/engine`; every suite green in CI.
- [ ] `docs/ledger.md` and `docs/deposition.md` complete enough for a stranger
      to reimplement both verifiers from scratch in another language.
- [ ] The engine has **no DELETE statement anywhere in it.** A CI grep
      enforces this. Destruction is the Warden's job alone.
**Est: 7 days. The most important phase — 3.4, 3.5, and 3.6 are the product.**
