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

**Not yet true of the deployed API Lambda, stated plainly (found 2026-08-09):**
`KmsKeyProvider` — real per-tenant CMKs, real `ScheduleKeyDeletion` with AWS's
mandatory 7-day pending window — is fully implemented and 100%-covered by
`tests/warden/test_keys_kms.py`, and three real CMKs are provisioned and
granted to the API Lambda's IAM role (`infra/api/exec-policy.json`'s
`EnvelopeEncryptionPerTenantCmk` statement, one ARN per demo tenant). But
`services/api/src/mnemos_api/runtime.py::build_runtime()` unconditionally
constructs `LocalKeyProvider()` and a single tenant-agnostic
`Envelope(LocalKeyWrapper())` shared across every request — it never reads
the `MNEMOS_KMS_KEY_ARN_CLINIC`/`_OPS`/`_FINANCE` env vars `.env.example`
documents, and never constructs a `KmsKeyProvider`. `shred` against the
deployed API today destroys an in-memory local key that resets on the next
cold start, not a durable AWS key — a materially weaker guarantee than the
`KmsKeyProvider` table above describes, on infrastructure that already exists
to close this gap. Wiring it requires more than swapping `LocalKeyProvider`
for `KmsKeyProvider`: `Envelope`'s `KeyWrapper` protocol wraps/unwraps for
*one* key, chosen when the `Envelope` is constructed, and `runtime.py` builds
exactly one `MnemosEngine` shared across every tenant's requests — genuine
per-tenant CMKs need either a per-tenant engine cache (the pattern
`tests/warden/conftest.py::_LiveKeyWrapper` already uses, per-test, but never
ported to production) or a tenant-aware `wrap`/`unwrap` call, neither of
which is a small change. Tracked, not silently left for a reader to discover
by diffing the IAM policy against the code.

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

**Tamper-evident within one checkpoint epoch — not tamper-proof, and this is
now proven against real infrastructure, not aspirational.**

Hash chaining alone catches a single edited or deleted row immediately. It does
*not* catch a principal with full database DML rights who rewrites an entire
shard's entries **and** the `chain_checkpoints` row that describes it,
consistently — that forgery is internally consistent, and we proved it: run
`mnemos-verify` against such a forgery and it reports **VALID**
(`tests/warden/test_attestation.py::test_attacker_who_fools_the_database_does_not_fool_the_anchor`,
`make test-aws`).

What catches it is `mnemos-attest verify`, which compares the live chain
against a Merkle root anchored to a real S3 bucket
(`mnemos-ledger-anchor-582054875648`) with Object Lock in **COMPLIANCE mode,
7-day retention** — created 2026-08-08, ADR-013. Objects there cannot be
altered or deleted by anyone, including AWS account root, before
2026-08-15. So the detection window for a whole-shard-plus-checkpoint forgery
is bounded by how often `mnemos-attest anchor` runs (a manual/scheduled step;
not yet on an EventBridge schedule — that lands with Phase 05's scheduling
infra), not by the in-database checkpoint interval alone.

**Empirically verified WORM behaviour, precisely stated:** a bare
`DeleteObject` on a locked object does not error — S3 versioning turns it into
a *delete marker* that hides the object while the locked version underneath is
untouched. `DeleteObject` against the specific locked version ID returns
`AccessDenied`, even with `--bypass-governance-retention` (which only applies
to GOVERNANCE mode; COMPLIANCE mode has no bypass for any principal). This
means a delete-marker attack against an anchor manifests as "anchor not found"
in `mnemos-attest verify`, not as "anchor disagrees" — a different failure
signature worth knowing before reading a NoSuchKey error as anything other
than an attack attempt or an operational mistake.

**A DDL-capable principal can remove the enforcement.** Invariant 2 is enforced
by a database trigger. Someone with `DROP TRIGGER` rights can remove it and then
write unaudited rows. Application roles are denied this (tested in
`tests/invariants/test_invariant_1_privileges.py`), but a database owner is not.
Anchored checkpoints are the defense against the *consequence* of that (a
rewrite that goes undetected); they do not prevent the trigger from being
dropped in the first place, only make the resulting gap provable after the fact.

**Not yet true, stated plainly:** anchoring does not yet run on a schedule —
today it is `mnemos-attest anchor`, invoked manually or from CI. A tenant that
is never re-anchored after new writes has no anchor covering that new state,
and `mnemos-attest verify` only proves what it was last asked to anchor.
Scheduled anchoring (EventBridge, alongside the checkpoint interval) is Phase
05/11 work, not yet built.

**KMS keys, similarly.** `KmsKeyProvider.destroy()` (real `shred`, Phase 06.4)
calls AWS's `ScheduleKeyDeletion` with the required minimum 7-day pending
window — genuinely slower than `LocalKeyProvider`'s instantaneous destruction,
and deliberately not worked around. `is_destroyed()` means "deletion has been
scheduled and the key can no longer be used", not "the key material is
already gone" — those are different instants, 7 days apart, and both matter
depending on which claim you are checking.

---

## Deployment topology

**The Warden is a library running inside the API Lambda, not a separate
service.** PHASE_06_GOVERNANCE_WARDEN.md's sub-phase 6.1 calls for
`services/warden`: its own Lambda, its own IAM role, and — critically — the
*only* role in the account holding `kms:ScheduleKeyDeletion` and reaching the
database's DELETE-capable login. That service does not exist; `services/warden`
was never created. What exists instead, and is real: two separate CockroachDB
roles (`mnemos_api` with no DELETE anywhere, `mnemos_warden` with it,
migration 011) and two separate `Database` connections inside the ONE Lambda
process (`Runtime.db` / `Runtime.warden_db`, gated by an admin-scope check
before the privileged one is ever touched).

**What this does and does not buy.** Two roles at the database layer means a
SQL-injection-shaped bug in an ordinary read/write path cannot reach DELETE —
that boundary is real, tested (`tests/invariants/test_invariant_1_privileges.py`),
and would hold even under a compromised query. What it does not provide: if
an attacker achieves arbitrary code execution *inside the Lambda process
itself* (a dependency vulnerability, a deserialization bug, a supply-chain
compromise), they inherit both database connections and both sets of
credentials, because both live in the same process's memory and the same
IAM role's environment. A genuinely separate Warden Lambda would put an IAM
boundary between "compromise the code that talks to agents" and "hold the
one credential that can destroy anything" — the same reasoning ADR-002 gives
for splitting the Warden out of the Fabric in the first place, just not
carried all the way to the process boundary.

**Why this is written up rather than fixed here (assessed 2026-08-09):** the
application code is already structured for the swap — `services/api`
touches the Warden exclusively through `Runtime.warden`'s stable method
interface (`forget`, `shred`, `revoke_source`, ...), so a `RemoteWarden`
adapter that calls a separate Lambda via `lambda:InvokeFunction` instead of
running `packages/warden.Warden` in-process would not require touching
`tools.py` at all. What is missing is genuinely new infrastructure, not a
refactor: a new Lambda function and container image, a new IAM role and
policy (KMS `ScheduleKeyDeletion` + the `mnemos_warden` DB login, and nothing
else), removing any equivalent reach from the API role, and redeploying —
comparable in size to the sleep-cycle Lambda's own deployment work earlier in
this build. Worth doing; not done in this pass, in favor of the entirely
unbuilt work (the Custodian, Phases 07 onward) that this project's remaining
time is better spent on.

---

## The Custodian's credential is not platform-enforced read-only

PHASE_07_CUSTODIAN.md 7.2 calls for a service account that is "**read-only
asserted at startup**" against the CockroachDB Cloud MCP server. Tested
against the real `mnemos` cluster (2026-08-09), against every Cloud IAM role
available to a service account:

| Role tried | Cloud API calls (`get_cluster`, `list_clusters`) | SQL tools (`list_databases`, `show_running_queries`, `list_tables`, ...) |
|---|---|---|
| Cluster Monitor | work | **all `unauthorized`** |
| Cluster Developer | work | **all `unauthorized`** |
| Cluster Admin | work | **work — including `create_database`** |

No role between Monitor/Developer and Admin exists to test against a service
account for this integration. The two systems are genuinely separate —
CockroachDB's own docs confirm "SQL users are granted a distinct set of
roles and privileges... independent of the Cloud user roles" — and the
cluster's real SQL Users (the ones with actual database grants:
`mnemos_api_svc`, `mnemos_pipeline_svc`, `mnemos_warden_svc`) have no
corresponding entry for the Custodian's service account at all; there is no
console surface found that binds a Cloud API service account to a scoped SQL
identity for this MCP integration.

**What this means:** the Custodian's actual credential is Cluster Admin —
capable of `create_database`/`create_table`/`insert_rows` if asked, verified
directly (a real, harmless scratch database was created and had to be
dropped by hand, since the client holds no DELETE/DROP capability by
design). The read-only *guarantee* PHASE_07 asks for is therefore enforced
entirely at the application layer, not the platform layer:
`mnemos_custodian.allowlist` never maps any skill to a write-capable tool
(checked statically, `tests/custodian/test_allowlist.py`), and
`CustodianMcpClient.call_tool()` independently refuses to invoke any of
`create_database`/`create_table`/`insert_rows` by name regardless of what
any allowlist entry says (`ReadOnlyGuaranteeViolated`, tested against both a
fake and the real live server). Two things would have to be wrong at once —
a bad allowlist entry *and* that backstop removed — for the Custodian's own
code to attempt a write. That is a real, tested guarantee; it is a weaker
one than "the account itself cannot write even if asked," and this page
says so rather than implying otherwise.

---

## `ccloud` CLI cannot run non-interactively

PHASE_07_CUSTODIAN.md 7.5 calls for the Custodian to "shell out to `ccloud`
with a scoped service account" for control-plane facts the MCP server
cannot provide (backup recency, region topology, cluster inventory).
Installed and inspected directly (`ccloud` v0.8.23, 2026-08-09): the binary
has exactly one authentication path, `ccloud auth login`, which opens a
browser and requires a human to paste back an authorization code
(confirmed by actually running it: it prints a URL and blocks on stdin for
the code). No `--api-key` flag, no documented environment variable, and the
binary's own strings show only the browser-login code path
(`ServeBrowserLoginServer`, `requestToken`) — there is no non-interactive
service-account login mode in this CLI. A Fargate task has no browser and
no human present when a scheduled sweep runs at 3am; this CLI cannot
authenticate there as shipped.

**What was built instead:** `mnemos_custodian.cloud_api.CloudApiClient`
calls the CockroachDB Cloud REST API directly (`GET /api/v1/clusters/
{cluster_id}/backups-config`, `GET /api/v1/clusters/{cluster_id}/backups`),
confirmed from the API's own published OpenAPI spec
(`https://cockroachlabs.cloud/assets/docs/api/latest/openapi.json`) to use
simple Bearer-token auth — the same service-account key already used for
the Cloud MCP server, verified live against the real `mnemos` cluster. Same
data (the `ccloud` CLI is itself a thin wrapper over this same API), same
credential, same deployability story as `mcp_client.py`. `custodian_findings
.tool_source = 'ccloud'` (migration 008's own naming) still means "control-
plane facts distinct from the MCP server's SQL-shaped ones," not "produced
by the literal `ccloud` binary" — worth knowing if a reader goes looking for
the binary in the deployed container and does not find it.

Region topology and cluster inventory are not separately re-implemented
here: `mcp_client.py`'s `get_cluster`/`list_clusters` tools already return
the same `regions` data this REST endpoint would. The one genuinely new
capability this section adds is backup recency
(`check_backup_recency`) — the concrete example PHASE_07 7.5 itself names.

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

## Dual control

**A second distinct API key, not a second distinct human.** Enabling
`mnemos.tenants.dual_control` requires two different admin key IDs to approve
a `forget`/`shred`/`redact`/`quarantine`/`revoke_source`/`set_legal_hold`
call against the same target before it executes — but nothing in this system
binds a key to the person holding it. Two admin keys issued to, and used by,
the same person satisfy the check as completely as two keys held by two
different people. The control it verifies is "a second credential approved
this," not "a second person reviewed this" — the latter is an organizational
practice a key-issuance policy has to supply, not something the software can
enforce on its own.

**The approval window is 15 minutes, and expiry is silent to the second
caller.** A first approval that nobody acts on within that window is simply
gone; the next call against the same target is treated as a fresh first
approval, not told that an earlier one existed and lapsed. This is
deliberate — a stale approval should not become a standing authorization
just because someone eventually got around to it — but it means a legitimate
two-person workflow that takes longer than 15 minutes between approvals will
need to start over.

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
