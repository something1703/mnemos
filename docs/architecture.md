# Architecture

One CockroachDB cluster, four planes, exactly one of which contains a model.

```
                          ┌─────────────────────────────────────────┐
                          │            CockroachDB Cloud             │
                          │   (episodes · facts · provenance · ledger)│
                          └───────┬──────────┬──────────┬───────────┘
                                  │          │          │
        writes, 0 AI work        │          │          │  destructive ops only
        ┌─────────────────────┐  │          │          │  ┌──────────────────────┐
        │   THE FABRIC         │◄─┘          │          └─►│   THE WARDEN          │
        │   (Lambda, MCP API)  │             │             │   (Lambda, own role)  │
        │   remember()          │             │             │   residency · holds   │
        │   recall()             │             │             │   erasure · revoke    │
        └───────────┬──────────┘             │             │   no model, ever       │
                    │                        │             └──────────────────────┘
        async, on a schedule                 │
        ┌───────────▼──────────┐             │
        │   THE SLEEP CYCLE     │             │            ┌──────────────────────┐
        │   (Step Functions)    │             │            │   THE CUSTODIAN        │
        │   distill · corroborate│            │            │   (ECS Fargate)        │
        │   promote · decay      │            └────────────┤   Cloud MCP + ccloud   │
        │   (Bedrock here)       │  findings, unverified    │   Agent Skills, RO      │
        └────────────────────────┘  like everything else    │   (Bedrock here)       │
                                                              └──────────────────────┘
```

Two boxes call a model — the sleep cycle and the Custodian. Neither can reach
the Warden's role, and both write everything they produce back into the
Fabric as `unverified`, subject to the same corroboration gate as anything an
agent wrote. `make no-model-in-warden` and `make no-warden-in-custodian`
enforce the two edges this diagram deliberately does not draw, statically, in
CI — not as a comment, as a build failure.

## The write path does zero AI work

`remember()` (`packages/engine`, served over MCP by `services/api` on Lambda)
encrypts an episode, homes it to a region via `REGIONAL BY ROW`, and commits
it in the same transaction as its ledger row. No model is on this path —
memory intake survives a total model-provider outage, because nothing on the
write path calls one. Interpretation happens later, asynchronously, in the
sleep cycle.

## The sleep cycle turns episodes into facts, on a schedule

`services/sleep-cycle` runs on Step Functions: distill episodes into
candidate facts, run the corroboration gate (exact maximum bipartite
matching over provenance edges — see [docs/trust.md](trust.md)), promote what
earns it, decay what goes stale. Everything it writes lands `unverified` like
anything an agent wrote; only a `system`/`operator`-provenance episode or two
independently-sourced episodes earn promotion. This is the one place Bedrock
is called on the write side, and it is structurally incapable of promoting
its own output — promotion is arithmetic in `packages/engine`, not a model's
opinion of itself.

## The read path is trust-gated and logged

`recall()` returns facts at or above the trust the caller is entitled to,
withholds `unverified` facts by default (reporting how many were withheld
rather than silently omitting the count), and — if the region matters —
answers with policy-approved derived projections rather than raw
cross-border content. Every `recall()` call appends its own ledger row, so a
later `explain(action_id)` can reconstruct exactly which recall fed which
decision.

## The Warden is the only thing that can destroy anything

`packages/warden` holds residency enforcement, legal holds, and every
erasure mode (`quarantine` / `forget` / `shred` / `crypto-shred`). It
contains no LLM SDK dependency — checked statically in CI on its own import
graph, not by convention. Privilege separation is enforced at the database
layer, not by a second Lambda function: the API Lambda's ordinary code path
connects as `mnemos_api_svc` (`MNEMOS_DB_URL`), a role holding no `DELETE`
anywhere in the schema; the Warden's own code path connects separately as
`mnemos_warden_svc` (`MNEMOS_DB_URL_WARDEN`), the only role that has it. Both
facts are verified at runtime, not assumed from config — `/health` asks
CockroachDB directly which grants the connected role actually holds and
reports `privilege_separation_source: "measured"` when it does. `revoke_source()` computes the
transitive blast radius across facts, corroborations, skills, past recalls,
and declared actions, and revokes all of it in one serializable transaction.

## The Custodian watches the cluster, and only proposes

`services/custodian` runs on ECS Fargate, scheduled and alarm-triggered. It
is the only component that talks to the CockroachDB Cloud MCP server and the
`ccloud` CLI, and it does so read-only: allowlisted SQL extracted from five
official [CockroachDB Agent Skills](https://github.com/cockroachlabs/cockroachdb-skills)
(vendored and pinned — see [docs/attribution.md](attribution.md)), a
structural backstop (`ReadOnlyGuaranteeViolated`) that fires before any
write-shaped tool call reaches the network, and a proposals table it can
`INSERT` into and nothing else — it cannot import `mnemos_warden` at all
(`make no-warden-in-custodian`). Findings enter memory the same way anything
else does: `unverified`, corroborated or not on the same terms as an agent's
own output.

## Every state-changing op is in the ledger, in the same transaction

Sharded hash chains (16 shards by default), each row linking to the previous
row in its shard by hash, checkpointed with a Merkle root anchored to S3
Object Lock (compliance mode) on a schedule. A database trigger — not
application code — refuses any `INSERT`/`UPDATE`/`DELETE` on a governed table
that does not append a matching audit row in the same transaction, so the
guarantee holds even against a session with raw SQL access. Verification is
two-layered on purpose: `mnemos-verify` recomputes the live chain from the
database (catches ordinary tampering); `mnemos-attest verify` compares it
against the S3-anchored root (catches an attacker who rewrites the chain
*and* its own checkpoint row consistently) — see
[docs/limits.md](limits.md) for exactly what each layer does and does not
catch.

## Why one database, not three services

The alternative architecture — a vector store, a graph database for
provenance, and a separate audit log — has a consistency gap between every
pair of those systems, and blast-radius revocation, atomic erasure, and a
deposition's hash-verified chain all *require* that gap not exist. Putting
vectors (C-SPANN, prefix-scoped by `tenant_id`), the provenance graph, and
the audit ledger in one CockroachDB cluster, under one serializable
transaction boundary, is what makes those three guarantees expressible at
all — not a preference, a requirement the guarantees impose backward onto
the architecture. See [docs/decisions.md](decisions.md) for the full ADR log
behind every choice on this page.
