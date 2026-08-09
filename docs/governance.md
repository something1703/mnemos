# Governance

What the Warden (`packages/warden`) actually does, for a reader who wants to
know how to use it rather than why to trust it — `docs/security.md` is that
second question. Every operation described here requires an authenticated
`admin` scope, an explicit `confirm=true`, and a stored `reason`; none of the
three is optional or a formality (`packages/warden/src/mnemos_warden/
warden.py`).

Last verified 2026-08-09.

---

## 1. Residency: where a subject's data may physically live and cross

A `residency_policies` row maps a subject-key pattern (`patient:*`,
`user:eu:*`) to a home region and a **projection**: what, if anything, a
request from outside that region may see.

| Projection | What crosses the border |
|---|---|
| `none` | Nothing. A foreign request gets an empty result and a logged refusal. |
| `derived` | Policy-approved derived facts only. Raw episode content never leaves. |
| `aggregate` | Aggregate statistics only, no individual fact. |

Enforcement runs on **every** `recall()`/`recall_as_of()` call, per fact —
two facts in one recall can be homed in different regions with different
policies — and every crossing, permitted or refused, writes a
`region_crossings` row naming the policy that decided it
(`packages/warden/src/mnemos_warden/residency.py`). `where_is(subject_key)`
returns the physical region and governing policy for a subject on demand.

**Deployment-dependent guarantee, stated plainly:** on the 9-node local rig,
`episodic_events`/`semantic_facts` are `REGIONAL BY ROW` and physically
partitioned. On the single-region Cloud Basic cluster this project is
deployed to, `home_region` is an ordinary column and the same policy is
enforced entirely by the Warden's read path — a real, tested guarantee, but a
weaker one than physical partitioning. `db/scripts/where_is.py` and
`docs/limits.md` never conflate the two.

## 2. Legal hold: the constraint that outranks erasure

`set_legal_hold(subject_key, matter_reference)` blocks every erasure mode
against that subject until released, citing the matter reference in the
refusal. A hold placed **between** an erasure preview and its confirmation is
still caught — the check runs inside the same transaction as the
destruction, not before it (`packages/warden/src/mnemos_warden/holds.py`).
`release_legal_hold` requires its own `confirm`+reason. A subject under hold
is still entitled to have stale, unrelated data quarantined by the sleep
cycle's TTL sweep — a hold blocks erasure specifically, not every trust-state
change.

## 3. Erasure: four modes, not a boolean

| Mode | What happens | Reversible? |
|---|---|---|
| `redact` | Content tombstoned; the row and its audit history remain | No (the content is gone) |
| `forget` | Episodes, facts, vector index entries, and provenance edges removed atomically | No |
| `quarantine` | Everything retained, revoked from recall | **Yes** |
| `shred` | `forget`, plus the tenant's KMS data key is destroyed (`ScheduleKeyDeletion`, AWS's mandatory 7-day pending window — not worked around) | No, and **tenant-wide**: every row this tenant has ever written becomes unrecoverable, not just this subject's |

The KMS key destruction row describes `KmsKeyProvider` as built and tested —
it is not yet what the deployed API Lambda actually uses (`docs/limits.md`
§Erasure has the honest version: the running service constructs
`LocalKeyProvider` unconditionally, so `shred` there destroys an in-memory
key, not a durable AWS one, as of this writing).

Every mode's preview (`preview_erasure`) runs the **same counting query** the
execution does — not a second query that is merely supposed to agree — so
there is no gap between what a caller was shown and what was actually
destroyed. `forget`/`shred` close the live-keyspace path; MVCC history
survives until `gc.ttlseconds` elapses (~75 minutes on the deployed Cloud
Basic cluster) or `shred` renders it unreadable; backups survive for the
backup retention period regardless, closed only by `shred`. Full table:
`docs/limits.md` §Erasure.

## 4. Revocation: blast-radius containment for poisoned sources

`revoke_source(source_event_ids, reason)` computes the transitive
contamination closure — episode → fact → dependent skill → recall → action →
laundered descendant episode
(`packages/engine/src/mnemos_engine/integrity.py`) — and, in one transaction:

- **Quarantines** every fact with no corroborating support left once the
  revoked source's evidence (and everything causally downstream of it) is
  set aside.
- **Demotes, rather than destroys**, every fact that is *also* corroborated
  by genuinely independent, non-revoked evidence — recomputing its
  corroboration count and trust from what remains. Over-revocation is
  treated as seriously as under-revocation
  (`packages/warden/src/mnemos_warden/revoke.py`).
- Quarantines every skill version that cited a now-quarantined fact.
- Marks every affected `recall_log` row and `action_log` row contaminated —
  so `explain()` on a decision made from since-revoked memory reports exactly
  that, with the specific revoked facts named.

`preview_revoke_source` shows the blast radius before anything is touched.
Nothing here deletes: a revocation is a claim that evidence should not be
trusted, not that the record of what happened should vanish. An operator who
later confirms real poisoning can `forget` explicitly.

## 5. Dual control: a tenant-configurable two-key rule

When `mnemos.tenants.dual_control` is enabled for a tenant, every operation
in §§2–4 above (plus `redact`/`quarantine`) additionally requires two
**distinct** admin keys before it executes:

1. The first admin's `confirm=true` call is refused
   (`DUAL CONTROL: <label> has approved ...`) and recorded as a pending
   approval against that exact operation and target.
2. A **second, different** admin key calling the identical operation against
   the identical target within 15 minutes consumes that approval and lets
   execution through.
3. The same key calling twice never satisfies it — a lone admin cannot
   approve their own action.

This is off by default and is a genuinely new control as of this hardening
pass (`packages/warden/src/mnemos_warden/approvals.py`,
`tests/warden/test_approvals.py`, `tests/api/test_dual_control.py`). Read
`docs/limits.md`'s Dual control section before relying on it operationally —
it proves a second distinct *key* approved, not a second distinct *human*.

## 6. Attestation: proof that survives a rewritten database

Every ledger checkpoint's Merkle root can be anchored to S3 Object Lock in
compliance mode (`mnemos-attest anchor --tenant <slug>`), where it cannot be
altered or deleted by anyone — including the AWS account root — before the
7-day retention period expires. `mnemos-attest verify` compares the live
chain against that anchor and is the only thing that catches a whole-shard
rewrite consistent within CockroachDB itself; `docs/ledger.md` §5 and
`docs/security.md` §3 explain why in-database hash chaining alone cannot.

`explain()`'s deposition names the covering checkpoint and, once anchored,
includes `anchor_presigned_url` — a time-limited link that lets a caller
fetch and independently verify the anchor without ever holding an AWS
credential (`mnemos-attest presign`, wired into `explain()` as of this
hardening pass).

`GET /v1/deposition/{action_id}/export.html` renders the same deposition as
a single, self-contained HTML file that reverifies its own hashes **offline,
in the browser** — no CDN, no build step, no Mnemos code trusted to check
its own math. It embeds the complete audit-chain segment (genesis to head)
for every shard the action's own audit trail touches, plus the covering
checkpoint's full `shard_heads`, and reimplements `docs/ledger.md`'s hash
construction a second, independent time in JavaScript
(`services/api/src/mnemos_api/deposition_html.py`). The one step that needs
network access — comparing against the S3-anchored root — is an explicit,
separate button, not silently bundled into "offline verify": an exported
file cannot re-read the live database, so what it proves is "this export's
own chain segment and checkpoint are internally consistent, genesis to
head," not "the database has not been touched since." `mnemos-attest
verify` against a live connection is what proves the latter.

## 7. What is not yet built

The Custodian (Phase 07) — an LLM agent that may *propose* governance actions
(a forget, a hold, a policy change) but never execute them — is planned but
not implemented as of this writing (`services/custodian` is a skeleton
package). `mnemos.governance_proposals` exists in the schema for it. Nothing
in this document describes capability that isn't real today; this section
exists so that absence is stated rather than left to be discovered.
