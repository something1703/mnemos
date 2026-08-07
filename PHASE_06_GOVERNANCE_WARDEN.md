# Phase 06 — The Governance Plane (the Warden)

**Objective:** `packages/warden` + `services/warden` — the only component in
Mnemos that can destroy anything or change policy, and the only component
that contains **no model call whatsoever**. Residency enforcement, legal
holds, the four erasure modes, blast-radius revocation, crypto-shredding, and
WORM-anchored attestation all live here.

This phase is the differentiation. If everything else shipped and this did
not, Mnemos would be one more memory store.

## Inputs needed from the user — ASK BEFORE STARTING
1. Approval of the KMS key policy: the Warden's role gets
   `ScheduleKeyDeletion`; **no other principal in the account does.**
2. Approval to create an **S3 bucket with Object Lock in compliance mode**.
   The user must understand and confirm: objects written there genuinely
   cannot be deleted or altered by anyone — including the account root —
   until the retention period expires. Suggest a 30-day retention for the
   hackathon (long enough to be real, short enough to be cheap). Do not
   create this bucket without explicit confirmation.
3. Confirm the two-person rule setting for `forget` in demo tenants.

## Sub-phase 6.1 — Warden service skeleton & the no-model guarantee
- [ ] `services/warden`: a separate Lambda (and a separate container for local
      runs) with its own IAM role. Only this role holds DELETE on the database
      and `ScheduleKeyDeletion` on KMS.
- [ ] **Hard guarantees, each enforced and each tested:**
      - the Warden's execution role has an explicit `Deny` on
        `bedrock:InvokeModel` and every other model-invocation action;
      - `packages/warden` declares zero AI SDK dependencies, enforced by a CI
        dependency check that fails the build if boto3's bedrock client, an
        LLM SDK, or an HTTP call to a model endpoint appears in its import
        graph — transitively;
      - a startup self-check asserts both and refuses to serve if either
        fails.
- [ ] Every Warden operation requires: an authenticated admin scope, an
      explicit `confirm`, a stated `reason` string (stored, not optional),
      and — where dual control is enabled — a second distinct admin key.
**Accept:** the dependency check fails a deliberately-poisoned branch that
imports an LLM client into the Warden; the IAM deny is proven by an attempted
Bedrock call from the Warden's role.

## Sub-phase 6.2 — Residency enforcement (pillar I)
- [ ] `residency_policies`: per (tenant, subject class) → home region, plus a
      `projection_policy` declaring what MAY cross a border: nothing, derived
      facts only, or aggregate statistics only.
- [ ] Enforcement middleware in the read path: a request originating in region
      X for a subject homed in region Y is served **only** the projection its
      policy allows; raw episode content is never returned across a boundary.
      Every crossing writes a `region_crossings` row naming the policy that
      permitted it. Denials are logged just as loudly as permissions.
- [ ] `where_is(subject_key)` promoted from a script to a supported API,
      returning physical region per row plus the governing policy.
- [ ] Test matrix on the 9-node rig: 3 requester regions × 3 subject regions ×
      3 projection policies = 27 cases, each asserting exactly what crossed
      and what was logged.
**Accept:** all 27 cases green; a raw-content leak across a boundary is
impossible to produce even with a crafted API call.

## Sub-phase 6.3 — Legal hold (the constraint that proves this is real software)
- [ ] `set_legal_hold(tenant, subject_key, reference, expires_at)` — places a
      hold citing an external matter reference.
- [ ] A held subject: cannot be forgotten, cannot be TTL-expired, and gets its
      ranges' `gc.ttlseconds` extended so `recall_as_of` covers the retention
      obligation.
- [ ] `forget` against a held subject **fails loudly** with the hold
      reference, the holder, and the expiry — never silently, never partially.
      A partial erasure under hold would be worse than either outcome.
- [ ] Holds are themselves audited, and releasing a hold is a separate audited
      admin operation.
- [ ] Test: hold → forget attempt → typed refusal with reference → release →
      forget succeeds. Also: TTL pass leaves held episodes untouched.
**Accept:** the four-step test green; the refusal carries every field a
compliance officer would need.

## Sub-phase 6.4 — The four erasure modes (pillar I + III)
Erasure is a spectrum, not a boolean. Each mode is one serializable
transaction, retry-wrapped, ending in an audit row.

| Mode | What happens | When you use it |
|---|---|---|
| `redact` | Content ciphertext tombstoned; row, provenance, and audit history retained | The content is harmful but the *record that it existed* must survive |
| `forget` | Episodes, derived facts, vector index entries, provenance edges deleted atomically; audit row retained | GDPR Art. 17 erasure request |
| `quarantine` | Rows retained, revoked from all recall, marked umbra | Suspected-bad data under investigation, or data under hold that must stop influencing decisions |
| `shred` | `forget` + destroy the tenant/subject data key in KMS, rendering backup- and MVCC-resident ciphertext permanently unreadable | Full erasure that must survive backups |

- [ ] `forget` implements the exact ordered transaction documented in the
      schema: count → delete episodes → delete tagged facts → delete vector
      entries (implicit but *asserted*) → purge provenance-orphaned facts →
      audit row. ONE transaction.
- [ ] Immediately-after test: recall for the forgotten subject returns
      nothing — **including via raw vector similarity against a
      pre-computed embedding of the deleted text.** Proving no orphaned
      embedding survives is the claim that separate vector stores cannot make.
- [ ] Partial-failure test: inject a fault before the audit append → the whole
      transaction rolls back and the data is still present. Erasure is atomic
      or it does not happen.
- [ ] `shred` test: shred a scratch tenant, then attempt to read its rows from
      a **restored backup** and prove the ciphertext is unreadable. This is
      the test that makes the erasure claim honest end to end; it is worth the
      day it will take.
- [ ] Every mode's preview (`what would this destroy?`) is exact and is
      computed in the same transaction as the destruction when confirmed —
      no TOCTOU gap between preview and action.
**Accept:** all four modes tested; the vector-orphan test and the backup-shred
test are both green and both filmed.

## Sub-phase 6.5 — revoke_source: blast-radius revocation (pillar III)
The operation nobody else can offer.

- [ ] `revoke_source(episode_id | source_selector, reason, confirm)`:
      1. compute the blast radius via `engine.blast_radius` (transitive:
         facts → dependent corroborations → skills → recalls → actions);
      2. in ONE serializable transaction: quarantine or delete every derived
         fact and its vectors, demote every corroboration that depended on
         them, quarantine every skill that cited them, and mark every past
         recall and action as **contaminated**;
      3. append the audit row with the full radius manifest;
      4. emit to the revocation changefeed so downstream consumers learn.
- [ ] **Contaminated actions are the payoff:** after revocation, `explain()`
      on an affected action reports "this decision was influenced by
      subsequently-revoked memory," with the exact revoked facts. That is an
      answer no agent platform can give today, and it is the thing a company
      actually needs the morning after an incident.
- [ ] Second-order test: revoke a source and prove that facts corroborated
      *only* by its descendants are demoted too, while facts with genuine
      independent support survive. Over-revocation is as much a bug as
      under-revocation — assert both directions.
- [ ] Idempotency and re-entrancy: revoking twice is a no-op; revoking a
      source whose descendants were already forgotten succeeds cleanly.
**Accept:** both directions of the second-order test green; a contaminated
deposition renders correctly; the changefeed subscriber receives the event.

## Sub-phase 6.6 — Attestation: anchoring proofs outside the database
A ledger a database administrator can rewrite is not a ledger.

- [ ] Every checkpoint's Merkle root is written to the **S3 Object Lock
      (compliance mode)** bucket as an immutable object keyed by
      `tenant/epoch`, together with the shard heads it binds.
- [ ] `mnemos-attest verify --tenant X --at T` fetches the anchored root from
      S3 and verifies the live database against it. If someone rewrote
      history in CockroachDB, the anchored root will not match and the tool
      says exactly where.
- [ ] The anchor object is public-readable for demo tenants so a judge can
      fetch the root themselves and verify our chain with their own code.
      Ship `scripts/independent_verify.py` — under 100 lines, importing only
      the standard library and boto3 — for exactly this.
- [ ] Tamper drill: rewrite a ledger row directly in the database as a
      superuser, then run attestation and show it caught. Film this.
**Accept:** the tamper drill is caught by the anchored root; the independent
verifier validates a real tenant with no Mnemos code imported.

## Sub-phase 6.7 — Depositions as a product surface
- [ ] `GET /deposition/{action_id}` returns the signed, verifiable structure
      from Phase 03.5, now including: residency at the time of each recall,
      trust state at the time of each recall, contamination flags, the
      governing checkpoint, and the S3 anchor URL.
- [ ] Export as a self-contained HTML file that renders and self-verifies
      offline — the thing you hand to an auditor or attach to an incident
      report. This artifact is the clearest single expression of what Mnemos
      is for; make it beautiful and make it work with no network.
**Accept:** an exported deposition opens offline, renders, and verifies its
own hashes in the browser.

## Definition of Done
- [ ] All four erasure modes, legal hold, residency enforcement, blast-radius
      revocation, and S3-anchored attestation implemented and tested.
- [ ] ≥90% coverage on `packages/warden`.
- [ ] The no-model guarantee proven three ways: CI dependency check, IAM deny,
      runtime self-check.
- [ ] `docs/security.md` and `docs/governance.md` complete, including the
      honest limits section (embedding leakage, GC windows, backup retention,
      what crypto-shred does and does not cover).
**Est: 7 days. Nothing in this phase may be cut.**
