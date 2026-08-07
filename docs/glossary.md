# Glossary

The vocabulary is small on purpose. If a term is not here, prefer an existing
one over inventing a new one.

**Episode** — one raw experience, written by `remember()`. Immutable, encrypted
at rest, homed to a jurisdiction, decays by Row-Level TTL. The only thing in
Mnemos that is directly observed rather than derived.

**Fact** — a durable claim distilled from one or more episodes by the sleep
cycle. Carries an embedding, a trust state, a strength, and a confidence. A fact
with no provenance edge is a bug, not a belief (invariant 3).

**Provenance edge** — the link from a fact to a source episode. The edges form
the graph that makes both depositions and blast radius computable.

**Trust state** — where a fact sits in the lattice: `unverified` →
`corroborated` → `trusted`, plus `contested` and `quarantined`. Everything an
LLM writes starts `unverified` and must earn promotion.

**Corroboration gate** — the rule that promotes a fact only on support from
*independent* sources: different session **and** different source-trust origin.
Two facts from the same poisoned session do not corroborate each other. This
definition is the security thesis; it lives in `trust.md`.

**Source trust** — declared at write time: `system`, `operator`, `agent`, or
`external`. Required, never optional, and defaults to the least-trusted value
the caller's scope allows.

**Contested** — two facts contradict each other with comparable evidence.
`recall()` returns both with their evidence rather than silently picking a
winner. An agent that knows it is unsure is more useful than one that guesses.

**Superseded** — an older fact displaced by better evidence. Never deleted;
supersession history is exactly what a deposition needs.

**Skill (procedural memory)** — a versioned playbook with fitness counters.
Agent-authored skills land quarantined: an agent must never be able to teach
itself an unvetted procedure and then execute it.

**Recall log** — an append-only record of which facts were returned to which
agent, when, at what score. Without it, `explain()` is impossible.

**Action** — something an agent did, declared with the recalls that caused it.
The bridge between memory and consequence.

**Deposition** — the verifiable causal chain behind one action: action ←
recalls ← facts *as they stood at that moment* ← provenance ← raw sources, every
node hashed, verified against the anchored Merkle root. Exportable as HTML that
verifies itself offline. What you hand a regulator.

**Blast radius** — the transitive closure of contamination from a source:
facts, dependent corroborations, skills, recalls, and actions. Computed before
anything is touched.

**Revocation** — `revoke_source()`: quarantine or delete everything in the blast
radius in one serializable transaction, mark affected actions **contaminated**,
and broadcast on the changefeed so downstream consumers learn.

**Residency** — the jurisdiction a subject's rows physically live in, enforced
by `REGIONAL BY ROW`. Raw content never crosses a boundary; only policy-approved
projections do, and every crossing is logged with the policy that allowed it.

**Legal hold** — a block on erasure citing an external matter reference. Beats
erasure, extends the GC window, and refuses *loudly*. A system that always
deletes on request is not compliant, only obedient.

**Erasure modes** — `redact` (tombstone content, keep the record), `forget`
(delete rows, facts, vectors, provenance atomically), `quarantine` (retain,
revoke from recall), `shred` (forget + destroy the KMS key so backup- and
MVCC-resident ciphertext becomes unreadable).

**Temporal recall** — `recall_as_of(t)`, implemented with `AS OF SYSTEM TIME`.
Bounded by `gc.ttlseconds`; fails loudly at the boundary rather than silently
answering from `now()`.

**Audit chain** — the hash-linked ledger, sharded by subject key so tenant
throughput scales instead of serialising on one row.

**Checkpoint** — a Merkle root over all shard heads, binding them into one
verifiable state and anchored to S3 Object Lock so a database administrator
cannot rewrite history undetected.

**The Fabric / The Ledger / The Warden / The Custodian** — the four planes.
The Warden is the only one that can destroy, and the only one guaranteed to
contain no model call. The Custodian is the only one that *is* an LLM agent, and
therefore holds the least privilege.
