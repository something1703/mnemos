# Limits

The page a skeptical engineer should read first. Every claim Mnemos makes has a
boundary; these are ours, measured rather than estimated, and stated plainly
enough to be uncomfortable.

Naming a limit is not a hedge. A system whose failure modes are undocumented is
not safer than one whose failure modes are published — it is only less
understood.

Last verified 2026-08-08 against CockroachDB Cloud Basic v26.2.5
(`mnemos`, aws-us-east-1) and a local v26.2.5 rig.

---

## Erasure

**What `forget` guarantees.** Episodes, derived facts, their vector index
entries, and provenance edges are removed in one serializable transaction, with
a hash-chained audit row appended in the same transaction. Either all of it
happens or none of it does.

**What the hash chain proves.** That the deletion was *recorded*, and that the
record has not been altered. It does not prove the bytes are gone.

**Where copies survive a `forget`:**

| Copy | Survives? | Closed by |
|---|---|---|
| Live keyspace | no | `forget` |
| Vector index entries | no | same transaction — this is the guarantee a separate vector store cannot make |
| MVCC history | **yes**, up to `gc.ttlseconds` | time, or `shred` |
| Backups | **yes**, for the backup retention period | `shred` |
| Changefeed consumers | **yes**, if already delivered | revocation broadcast; downstream honouring it is out of our control |

**`shred`** additionally destroys the tenant's KMS data key, rendering
backup- and MVCC-resident ciphertext permanently unreadable. This is the only
mode that closes the backup path, and Phase 10.5 tests it by restoring a real
backup of a shredded tenant.

**Embeddings leak.** A 1024-dimension embedding is a lossy but non-zero encoding
of its source text; inversion attacks against embeddings are an active research
area. This is exactly why erasure must delete vectors in the same transaction as
the row, and why crypto-shredding the text alone would be insufficient.

---

## Temporal recall

`recall_as_of(t)` is implemented with `AS OF SYSTEM TIME` and is bounded by the
cluster's MVCC garbage collection window.

| Cluster | `gc.ttlseconds` | Usable window |
|---|---|---|
| CockroachDB Cloud Basic (ours) | 4500 | **~1 hour 15 minutes** |
| Local Docker default | 14400 | ~4 hours |

**75 minutes is short.** Depositions about older decisions depend on Phase 06.3
extending `gc.ttlseconds` on the ranges holding subjects under legal hold or
audit flag. Zone configuration is available on Basic (verified by the Phase 02.1
probe), so this is implementable — but it is opt-in per subject, not a global
property, and a deposition for a subject that was never flagged will hit the
boundary.

The engine raises a typed `OutsideTemporalWindow` error carrying the real
boundary. It never silently answers from `now()`, which is the trap this feature
invites.

---

## The ledger

**Tamper-evident within one checkpoint epoch — not tamper-proof.**

Hash chaining catches a single edited or deleted row immediately. It does *not*
catch a principal who rewrites an entire shard and recomputes its internal
hashes; that forgery is internally consistent. Such a rewrite is caught only
when the shard's head is compared against a Merkle root already anchored in S3
Object Lock, so the detection window is up to one checkpoint interval
(default: 1 hour, configurable via `MNEMOS_CHECKPOINT_INTERVAL_MINUTES`).

Shortening the epoch shortens the window and costs one checkpoint write per
tenant per interval.

**A DDL-capable principal can remove the enforcement.** Invariant 2 is enforced
by a database trigger. Someone with `DROP TRIGGER` rights can remove it and then
write unaudited rows. Application roles are denied this (tested in
`tests/invariants/test_invariant_1_privileges.py`), but a database owner is not.
The anchored checkpoints are the defense: the chain notices the gap even when
the trigger is gone.

---

## Tenant isolation

**RLS defends against our bugs, not against a hostile SQL session.** Policies key
on the `app.tenant_id` session variable, so they stop the API from leaking data
when a query forgets its scope. They do not stop an adversary who already holds a
database connection and can set the variable themselves. The boundary against
*that* adversary is API-key-to-tenant resolution (Phase 04.2) plus network
access control — not RLS.

**What is genuinely strong:** the vector index is prefix-scoped by `tenant_id`,
so approximate-nearest-neighbour search is partitioned *inside* the index. The
query plan shows `vector search … prefix spans: [/'<tenant>' - /'<tenant>']`,
asserted in `tests/security/test_rls_isolation.py`. Filter-after-ANN designs both
leak and silently lose recall as tenants grow.

---

## Residency

**Physical homing requires a multi-region cluster.** On the 9-node local rig
(`make db-multiregion`), `episodic_events` and `semantic_facts` are
`REGIONAL BY ROW AS home_region` and rows are genuinely partitioned by
jurisdiction, with `SURVIVE REGION FAILURE`.

**Our deployed cloud cluster is single-region Basic.** There, `home_region` is
an ordinary column and residency is enforced by the Warden in the read/write
path — the same policy, a weaker guarantee. `db/scripts/where_is.py` prints
which of the two you are looking at, and never conflates them.

We do not claim a globally distributed production deployment. We claim a
demonstrated multi-region design and a single-region hosted demo.

---

## Memory poisoning

**Prevention is bounded; containment is the real control.**

Everything an LLM writes enters at `trust='unverified'` and is excluded from
recall. Promotion requires independent corroboration — different session *and*
different `source_trust`. So the **collusion threshold is 2**: an attacker who
controls two sources that look independent can promote a fact.

That number is a tenant-configurable policy (`promotion_policy`), not a law of
nature. Raising it trades poisoning resistance against how long legitimate
knowledge takes to become usable.

Prompt-level defenses (delimiting content, declaring it as data) fail eventually
and we assume they will. When prevention fails, `revoke_source()` enumerates the
transitive blast radius and revokes it in one transaction — which is why
containment, not prevention, is the claim we make loudly.

---

## Scale

Numbers land in `docs/scale.md` as Phase 11.3 measures them. Until then this
section makes no claims. Known shapes:

- **Audit chain throughput** scales with shard count (default 16). The
  per-shard head row is still a serialization point for subjects that hash to
  the same shard.
- **Blast radius** is a recursive CTE over provenance edges; cost grows with
  contamination depth, not tenant size. The curve is published in Phase 11.3
  because it is our most novel query and nobody else can publish one.
- **C-SPANN** behaviour at 1M facts is measured in Phase 11.3, including index
  build time.

---

## Regulatory framing

Mnemos is **designed against** GDPR Art. 17 / 22 / 44, EU AI Act Art. 12 / 86,
HIPAA §164.312(b), and India DPDP §12. It is not certified to any of them, no
part of it has been reviewed by counsel, and nothing here is legal advice.

The design intent is that a deposition and an erasure proof would be *useful
evidence* in demonstrating compliance — not that their existence constitutes it.

---

## What we have not tested

As of 2026-08-08: sustained load, 1M-vector recall latency, backup restore of a
shredded tenant, region-failure survival under live writes, and embedding
inversion. Each is scheduled (Phases 10 and 11) and each will be reported here
with its result, including results we dislike.
