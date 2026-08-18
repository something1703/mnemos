# Runbook

Detection → diagnosis → remediation, for the failure modes that actually
matter to this system's guarantees. Not a generic on-call checklist — every
entry here is a way one of the five invariants, or the deployment itself,
could look fine and not be. Start with `curl -s $MNEMOS_API_URL/health`; it
needs no credential and answers most of "is anything actually wrong" in one
call.

---

## `privilege_separation: false`, or `api_can_delete: true`

**Detection.** `/health`'s `posture` block, or `make smoke`'s first two
checks.

**What it means.** Invariant 1 is not held by the cluster the API is
connected to — the database role the API uses has `DELETE` on the memory
tables. This is measured at startup by asking CockroachDB directly
(`privilege_separation_source: "measured"`), not read from configuration, so
this cannot be a stale config value; it means the connection really does
have destructive rights.

**Remediation.** Point `MNEMOS_DB_URL` (API/sleep-cycle) at a role granted
only `mnemos_api` privileges, and confirm `MNEMOS_DB_URL_WARDEN` is the only
connection string granted `DELETE`. Redeploy; re-check `/health`. Do not
proceed with judging traffic or production writes while this reports false —
every other guarantee on this page assumes invariant 1 holds.

## `mnemos-verify` reports `VALID` but you suspect tampering

**Detection.** `mnemos-verify` (in-database chain recomputation) says
`valid: true`; `mnemos-attest verify` (S3-anchor comparison) says otherwise,
or an operator's independent suspicion doesn't match the in-database
verdict.

**What it means.** This is the exact gap `mnemos-attest verify` exists to
close — see [docs/limits.md](limits.md#the-ledger). An attacker with raw
`UPDATE` rights on the ledger table can rewrite a shard's rows *and* the
checkpoint row that describes them, consistently, and the in-database
verifier will recompute a chain that is internally self-consistent because
it was rewritten to be. It cannot detect this class of attack by
construction — nothing running inside the compromised database can.

**Remediation.** Never trust `mnemos-verify` alone for a judgment call that
matters. Run `mnemos-attest verify --tenant <slug>`, which recomputes the
chain and compares its Merkle root against the one anchored to S3 Object
Lock (compliance mode — immutable for the retention period, including
against the account that wrote it). A mismatch here is unambiguous evidence
of tampering between the last anchor and now; escalate as a security
incident, not a data-quality one. `scripts/independent_verify.py` repeats
this check with zero AWS credential, for a party who should not have to
trust Mnemos's own tooling at all.

## A checkpoint hasn't anchored recently

**Detection.** `GET /v1/checkpoints` — the latest entry's `anchored` is
`false`, or `anchored_at` is older than expected.

**What it means.** Anchoring is currently a scheduled/manual step, not
continuous — see [docs/limits.md](limits.md#the-ledger). Detection of the
attack above is bounded by how recently a checkpoint was anchored: tampering
between two anchors is invisible to `mnemos-attest verify` until the next
one lands.

**Remediation.** Run the anchoring job manually if it is overdue; if it is
failing, check the S3 Object Lock bucket's write permissions and the KMS key
used for the anchor object first — those are the two most common causes of a
silent anchoring failure. This is tracked as an open item, not hidden: see
`docs/limits.md`'s own admission that this is not yet on a fixed schedule.

## `forget()` / `shred()` refused unexpectedly

**Detection.** A destructive-scope call returns a legal-hold error instead
of succeeding.

**What it means.** Working as designed — invariant 5 says legal hold
outranks erasure and refuses loudly, citing the matter reference, rather
than silently no-op'ing or (worse) deleting anyway. This is not a bug to
route around.

**Remediation.** Confirm the hold is actually still warranted
(`GET /v1/holds?subject_key=...`); if it should be lifted, that is a
deliberate governance action through the Warden's own hold-release path, not
a workaround. If the hold is legitimate, the correct outcome is that erasure
stays refused.

## A source needs to be revoked (suspected poisoning)

**Detection.** An unexplained spike in `unverified`-trust volume from one
source, a red-team-style finding from the Custodian, or an operator
identifying a source as compromised out of band.

**What it means.** A source has produced enough plausible-looking claims
that they should be assumed compromised until proven otherwise. This is the
scenario `revoke_source()` exists for — see
[docs/redteam.md](redteam.md) for what it actually blocks versus what it
cannot (the published collusion case).

**Remediation.** Call `revoke_source(source_id)` with the admin-scoped
credential. It computes the transitive blast radius — facts, corroborations,
skills, past recalls, and declared actions the source touched — in one
serializable transaction: a fact corroborated *only* by the revoked source's
descendants is quarantined; a fact also corroborated by genuinely
independent evidence survives, demoted to whatever that remaining evidence
earns. Check `GET /v1/depositions` for any decision that cites the revoked
evidence — those depositions now read "influenced by subsequently-revoked
memory," which is the signal to review the decisions themselves, not just
the memory.

## The Custodian's sweep is failing or stale

**Detection.** No new Custodian findings in memory past the expected
schedule interval; `services/custodian` logs a connection or tool-call
error.

**What it means.** Most commonly a Cloud MCP Server credential or IAM-role
issue — see [docs/limits.md](limits.md#the-custodians-credential-is-not-platform-enforced-read-only)
for exactly which IAM roles unlock which tools, since this is not obvious
from the Cloud Console alone (Cluster Monitor and Cluster Developer both
block every SQL-shaped tool; only Cluster Admin unlocks them, and the
Custodian's own service account is scoped to allowlisted read tools on top
of that role, not the role's full authority).

**Remediation.** Check `COCKROACH_MCP_URL`,
`COCKROACH_SERVICE_ACCOUNT_KEY_CUSTODIAN`, and `COCKROACH_MCP_CLUSTER_ID`
are current; confirm the service account's role in Cloud Console. If the
tool catalog comes back empty, the credential is connecting but under-scoped
— see `tests/custodian/test_mcp_client_live.py` for the exact live checks
this project runs against the real MCP server to catch this class of
failure before it reaches production.

## Consolidation lag is growing (Sleep Cycle falling behind)

**Detection.** `posture.chain_shards` and recent `episodes` count growing
while `facts` promotions stay flat; CloudWatch alarm
`mnemos-sleep-cycle-consolidation-lag`.

**What it means.** Episodes are being written (the write path has no
dependency on the sleep cycle, by design) but not being distilled into
recallable facts. `recall()` returning nothing for something you just wrote
is expected in the short term — see [docs/clients.md](clients.md)'s "things
that will look like bugs and are not" — but sustained growth means the
Step Functions execution is failing or under-scheduled, not just running
behind by a normal margin.

**Remediation.** Check the Step Functions execution history for failures
first (a Bedrock throttle or quota error is the most common real cause);
confirm `MNEMOS_MODEL_BUDGET_USD` has not been exhausted for the period. The
write path is unaffected either way — this is a staleness problem, not a
data-loss one.

## A tenant's data looks like it crossed into another tenant's view

**Detection.** Any console screen or API response showing data that does not
match the active tenant's key.

**What it means.** Treat this as a critical, stop-the-line finding — tenant
isolation is a core claim, not an implementation detail. This project has
shipped this exact class of bug once already this build: `callTool()`'s MCP
routing defaulted to a static read key regardless of which tenant was
selected, until the tenant-scoped resolution in
`apps/console/src/lib/api/mcp.ts` was fixed. That bug is what this entry
exists to make sure nobody has to rediscover.

**Remediation.** Confirm which credential actually served the request (every
API key is tenant-bound at mint time, not just labeled); check
`apps/console/src/lib/api/mcp.ts` and `apps/console/src/lib/api/server.ts`
both resolve the active tenant's key per-request rather than a shared
default. Re-run `make smoke` against the affected tenant's key to confirm
`remember`/`recall` scope correctly before treating the incident as closed.
