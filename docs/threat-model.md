# Threat model

Written **before** the code, on purpose (Phase 01.5). Phase 10 executes every
attack class below against our own deployed stack and publishes the results —
including the ones that succeed — in `redteam.md`.

Scope: infrastructure we own and deploy, in our AWS account and our CockroachDB
cluster, against dedicated `redteam-*` tenants. Nothing here targets third-party
systems.

## What we are protecting

1. **Confidentiality across tenants and across jurisdictions.** Tenant A must
   never observe tenant B, and raw content must never cross a border.
2. **Integrity of belief.** A fact an agent acts on should be traceable to real
   evidence, and contamination must be enumerable and reversible.
3. **Integrity of the record.** What the ledger says happened must be what
   happened, even against an insider.
4. **Erasure that means something.** When a subject is forgotten, it is gone
   from the live keyspace atomically, and `shred` closes the backup path.
5. **Availability of memory intake.** Agents may degrade; memory must not stop.

## Adversaries

| Adversary | Capability | Motivation |
|---|---|---|
| **External content author** | Can get text into an episode (a document, a tool result, a user message) | Persist an instruction that survives into future recalls |
| **Compromised agent** | Holds a valid `write` API key | Write memory, learn skills, cause destruction if it can escalate |
| **Curious tenant** | Valid credentials for their own tenant | Read another tenant's memory |
| **Malicious insider** | Database superuser | Rewrite history to hide an action |
| **Subject acting in bad faith** | Can request erasure | Destroy evidence under legal hold |
| **Careless operator** | Admin scope | Irreversible destruction by mistake |

## Attack classes (each is executed in Phase 10)

### 1. Memory poisoning — the defining risk
Prompt injection that *persists*. One malicious fact silently contaminates every
future recall, with unbounded blast radius over time.

- **Surfaces:** episode content; the consolidation distiller (highest value —
  its output is trusted downstream); the contradiction judge; the Custodian's
  interpreter; learned skills.
- **Controls:** content is delimited and declared as data in every prompt;
  `source_trust` is mandatory **and the two trusted-on-arrival origins are
  bound to the credential, not declared by the caller** — an injected agent on
  a write key cannot label its own claim `operator` and skip the gate
  (`keys.py`'s `Principal.may_declare`, added 2026-08-10 after this exact
  bypass was found; see docs/limits.md); everything an LLM writes lands
  `unverified`; the corroboration gate requires genuinely independent support;
  agent-authored skills are quarantined by default.
- **Honest limit:** prompt-level defenses fail eventually. The real control is
  the gate, and the real containment is `revoke_source`. We publish the
  **collusion threshold** — how many independent-looking sources an attacker
  must control to promote a fact — rather than implying it is infinite.

### 2. Cross-tenant exfiltration
- **Surfaces:** crafted SQL, subqueries, CTEs, `RETURNING`; **vector similarity
  search** (an unscoped ANN index will happily return another tenant's
  neighbours — the subtle one most systems get wrong); error-message oracles;
  recall-latency timing; MCP tool arguments carrying foreign IDs.
- **Controls:** vector index prefix-scoped by `tenant_id` so isolation lives
  *inside* the index; RLS as a backstop beneath the API middleware.
- **Test that matters:** disable the middleware and prove the database alone
  still holds. Defense in depth is only real if the second layer is verified.

### 3. Ledger tampering
- **Surfaces:** single-field edit; row deletion; **full shard rewrite with
  recomputed internal hashes**; checkpoint forgery; backdating.
- **Controls:** hash chaining per shard; Merkle checkpoints binding all shards;
  roots anchored to S3 Object Lock in compliance mode, where not even the
  account root can alter them.
- **Honest limit:** an internally consistent shard rewrite is caught **only** by
  the anchored checkpoint, so the detection window is up to one epoch. We
  publish the epoch. Claim: tamper-*evident*, never tamper-*proof*.

### 4. Residency violation
- **Surfaces:** direct read from a foreign region; consolidation batching that
  mixes regions; over-sharing recall projections; changefeed subscription from a
  foreign region; backup/restore into another region; the console.
- **Controls:** `REGIONAL BY ROW` homing from the subject's policy, not the
  writer's location; projection policies; every crossing logged.
- **Also tested:** changing a residency policy without admin scope, and changing
  it *retroactively* so past crossings appear legitimate.

### 5. Erasure evasion — and erasure abuse
Both directions, because they are equally real.

- **Evasion:** raw vector similarity against a pre-computed embedding of deleted
  text; `AS OF SYSTEM TIME` reads inside the GC window (**this will find it** —
  expected, documented, and the reason legal hold manages GC deliberately);
  backup restore; changefeed replay. `shred` closes the backup path.
- **Abuse:** a stolen `write` key triggering destruction; revocation of
  legitimate memory as a denial-of-memory attack; a spurious legal hold placed
  to block a lawful erasure. Each requires admin scope, dual control, and is
  loudly audited.

### 6. Availability and correctness under stress
- 40001 storms on one subject key; `forget` interleaved with concurrent
  `remember` and `recall` on the same subject (the hardest correctness property
  in the system); node kill mid-write; pipeline killed mid-execution; the model provider
  throttled to zero.
- **Design control:** the write path does zero AI work, so memory intake
  survives a total model outage. Proven, not asserted.

## Assumptions we are making

Stated so a reader can disagree with them:

1. AWS IAM boundaries hold. If the account is fully compromised, the S3 anchor
   is the last line and Object Lock is what makes it a line at all.
2. Embeddings leak information about their source text. Lossy, non-zero. This is
   precisely why erasure must delete vectors in the same transaction, and why
   crypto-shred alone is insufficient.
3. A cheap LLM judging contradictions can be wrong. Its output is therefore a
   data field, never an action — it can influence a state, never trigger a delete.
4. `gc.ttlseconds` bounds temporal recall. On our Cloud Basic cluster it is
   **4500s (~1.25h)** by default. Anything requiring a longer window needs an
   explicit zone-config change, which is why Phase 06.3 exists.

## Out of scope

Physical security; CockroachDB's own consensus correctness; AWS platform
compromise; a malicious maintainer of the upstream `cockroachdb-skills` repo
(mitigated only by pinning a commit and allowlisting tools).
