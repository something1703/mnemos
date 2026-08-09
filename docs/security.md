# Security

The entry point for "how does this actually hold up." It states what is
built and tested today, points at the document that proves each claim, and
says plainly where a claim stops. `docs/threat-model.md` is the adversary
list this was designed against; `docs/limits.md` is the longer, harder
version of the honest-limits section below; `docs/ledger.md` and
`docs/trust.md` are the specifications — "this document is the contract" —
for the two mechanisms this page only summarizes.

Last verified 2026-08-09 against CockroachDB Cloud Basic (`mnemos`,
aws-us-east-1) and the deployed AWS stack (Lambda, S3 Object Lock, KMS).

Claim only what a test proves (`AGENTS.md`'s own rule for this project). This
page follows it.

---

## 1. The five invariants, and how each is actually enforced

| # | Invariant | Enforced by | Proven by |
|---|---|---|---|
| 1 | No LLM-driven process ever holds DELETE or governance privileges | Database role grants (`mnemos_api`/`mnemos_pipeline`/`mnemos_readonly` never granted DELETE, migration 011); the Warden (`packages/warden`) contains zero LLM SDK dependency | `tests/invariants/test_invariant_1_privileges.py`; `make no-delete-in-engine` and `make no-model-in-warden` (static grep over the whole tree, not just the Warden, so a DELETE statement anywhere outside `packages/warden` fails the build); a runtime self-check (`assert_no_model_loaded()`, called at API startup) |
| 2 | Every state-changing memory op appends a hash-chained audit row in the same transaction | A `BEFORE INSERT/UPDATE/DELETE` trigger (migration 010) that refuses the mutation unless a ticket from `append_audit()` exists in the same transaction | `tests/invariants/test_invariant_2_audit.py`; `docs/ledger.md` §2–3 for the exact hash construction |
| 3 | No fact becomes recallable without provenance to at least one episode | A trigger on `semantic_facts` that blocks promotion to `corroborated`/`trusted` with zero `fact_provenance` edges | `tests/invariants/test_invariant_3_provenance.py` |
| 4 | Memory rows never leave their home region | `REGIONAL BY ROW` homing on the 9-node multi-region rig; Warden-enforced read-path projection (`enforce_recall_projection`) on the single-region Cloud deployment, wired into `recall()`/`recall_as_of()` | `tests/warden/test_residency.py`, `tests/api/test_recall_residency.py`; `docs/limits.md` §Residency for exactly which guarantee applies to which deployment |
| 5 | Erasure is atomic across rows, vectors, and provenance — or it does not happen | One serializable transaction per erasure mode; legal hold checked before every erasure and refused loudly, citing the matter reference | `tests/warden/test_erasure.py`, `tests/warden/test_holds.py` |

None of these are asserted only in prose — each row names the mechanism and
the test file that exercises it. Where a mechanism has a documented gap
(invariant 2's trigger can be dropped by a DDL-capable principal; see §3
below), that gap is stated, not hidden.

## 2. Governance: what the Warden can do, and what stops an admin key from misusing it

The Warden (`packages/warden`) is the only component in Mnemos that can
destroy anything or change policy, and the only one guaranteed to contain no
model call (ADR-002). Every governance-affecting call requires an
authenticated `admin` scope, an explicit `confirm=True`, and a stored
`reason` — none of the three is a formality; see `docs/governance.md` for
what each of the Warden's operations actually does and guarantees.

**Dual control**, new as of this hardening pass: a tenant may require two
distinct admin keys to approve `forget`/`shred`/`redact`/`quarantine`/
`revoke_source`/`set_legal_hold` before either executes. The first admin's
call is refused and recorded; a second, different admin key against the
identical operation and target is what lets it through
(`packages/warden/src/mnemos_warden/approvals.py`, `tests/warden/
test_approvals.py`). Honest limit: this proves a second distinct **key**
approved, not a second distinct **human** — see `docs/limits.md`'s Dual
control section.

## 3. The ledger: tamper-evident, not tamper-proof

Hash chaining alone catches a single edited or deleted row immediately. It
does **not** catch a principal with full database DML rights who rewrites an
entire shard's entries and the checkpoint that describes it, consistently —
that forgery is internally self-consistent. We proved this against real
infrastructure rather than asserting it
(`tests/warden/test_attestation.py::test_attacker_who_fools_the_database_does_not_fool_the_anchor`,
`make test-aws`): run `mnemos-verify` against such a forgery and it reports
**VALID**.

What catches it is `mnemos-attest verify`, comparing the live chain against a
Merkle root anchored to a real S3 bucket
(`mnemos-ledger-anchor-582054875648`) with Object Lock in **compliance mode,
7-day retention** (ADR-013) — objects there cannot be altered or deleted by
anyone, including the AWS account root, before the retention period expires.
`explain()`'s deposition now includes a presigned, time-limited URL to that
anchor (`anchor_presigned_url`) so a caller can fetch and verify it without
ever holding an AWS credential of their own.

**Stated limit:** a DDL-capable principal (a database owner, not an
application role) could drop the audit trigger itself. Anchored checkpoints
are the defense against the *consequence* — a rewrite that would otherwise go
undetected — not a defense against the trigger being removed in the first
place. Full detail, including the exact S3 delete-marker-vs-`AccessDenied`
behavior we measured: `docs/limits.md` §The ledger.

## 4. Memory poisoning: containment, not prevention

Everything an LLM writes enters at `trust='unverified'` and is excluded from
recall by default. Promotion to `corroborated` requires independent
corroboration — a different session **and** a different `source_trust`,
computed by maximum bipartite matching over a fact's provenance edges
(`packages/engine/src/mnemos_engine/corroboration.py`, `docs/trust.md`). The
**collusion threshold is 2**: an attacker who controls two sources that look
independent can promote a fact. That number is a tenant-configurable policy,
not a law of nature — raising it trades poisoning resistance against how long
legitimate knowledge takes to become usable.

When prevention fails anyway, `revoke_source()` computes the transitive
blast radius (episode → fact → skill → recall → action → laundered
descendant episode) and, as of this hardening pass, no longer destroys
everything it touches indiscriminately: a fact corroborated **only** by the
revoked source's descendants is quarantined; a fact **also** corroborated by
genuinely independent, non-revoked evidence survives, demoted to whatever
that remaining evidence actually earns
(`packages/warden/src/mnemos_warden/revoke.py`,
`tests/warden/test_revoke.py::test_revoke_source_second_order_demotes_survivors_and_quarantines_the_rest`).
Over-revocation is treated as seriously as under-revocation.

## 5. Tenant isolation

RLS policies key on a session-local `app.tenant_id` variable — they stop the
API from leaking data when a query forgets its scope, not a session that
already holds a raw database connection and can set the variable itself. The
boundary against *that* adversary is API-key-to-tenant resolution plus
network access control, not RLS.

What is genuinely strong: the vector index is prefix-scoped by `tenant_id`,
so approximate-nearest-neighbour search is partitioned **inside** the index,
not filtered after the fact — verified by inspecting the query plan itself
(`tests/security/test_rls_isolation.py`), not merely by asserting no rows
leaked.

## 6. Honest limits, in one place

The unabridged version, with numbers and dates, is `docs/limits.md`. The
five that matter most for a security review:

- **Embeddings leak.** A 1024-dimension embedding is a lossy but non-zero
  encoding of its source text. This is exactly why erasure deletes vector
  index entries in the same transaction as the row, and why crypto-shredding
  the text alone would not be sufficient.
- **`gc.ttlseconds` bounds temporal recall.** ~75 minutes on our deployed
  Cloud Basic cluster by default. `recall_as_of()` raises a typed
  `OutsideTemporalWindow` error carrying the real boundary rather than
  silently answering from `now()`.
- **Backups survive `forget`; `shred` is designed to close that path** by
  destroying the tenant's KMS data key (`KmsKeyProvider.destroy()`, a real
  `ScheduleKeyDeletion` call with AWS's mandatory 7-day pending window — not
  worked around) — but the deployed API Lambda does not construct a
  `KmsKeyProvider` at all as of this writing; it uses an in-memory local key
  that resets on cold start. The class is real and 100%-tested; the wiring
  from the already-provisioned per-tenant CMKs into `runtime.py` is not done.
  Full detail: `docs/limits.md` §Erasure.
- **The Warden runs inside the API Lambda, not as its own service.** Two
  database roles give real privilege separation against a SQL-injection-shaped
  bug; they do not protect against arbitrary code execution in the Lambda
  process itself, which would inherit both roles' credentials. A separate
  Warden Lambda with its own IAM role (PHASE_06 6.1's original design) closes
  that gap and has not been built. `docs/limits.md` §Deployment topology.
- **The Custodian's read-only guarantee is application-layer, not
  platform-layer.** Tested against the real cluster: no CockroachDB Cloud
  IAM role short of Cluster Admin unlocks the MCP server's SQL tools for a
  service account at all — Cluster Monitor and Cluster Developer both block
  every SQL-shaped tool outright, not just writes. The Custodian's actual
  credential is Admin, genuinely capable of `create_database` (verified
  directly). What actually stops it from writing is
  `mnemos_custodian.allowlist` plus `CustodianMcpClient.call_tool()`'s own
  refusal to invoke a write-capable tool by name, both tested — not the
  account's own privileges. `docs/limits.md` §The Custodian's credential.
- **Dual control proves a second key, not a second human** (§2 above).
- **What has not been executed yet, stated plainly:** adversarial
  red-teaming (Phase 10) and load/scale measurement (Phase 11) have not run
  against this deployment as of this writing. `AGENTS.md` commits this
  project to reporting those results, including the ones we dislike, once
  they exist — this page will be updated then, not before.

## 7. Regulatory framing

Mnemos is **designed against** GDPR Art. 17/22/44, EU AI Act Art. 12/86,
HIPAA §164.312(b), and India DPDP §12. It is not certified to any of them, no
part of it has been reviewed by counsel, and nothing here is legal advice.
The design intent is that a deposition and an erasure proof would be useful
*evidence* in demonstrating compliance — not that their existence
constitutes it.
