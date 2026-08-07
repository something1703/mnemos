# Phase 04 — Mnemos MCP API

**Objective:** Expose the engine and the Warden as an MCP server + REST
facade, deployed on Lambda behind API Gateway. After this phase, Claude Code,
Cursor, or any LangChain agent plugs into Mnemos with one config snippet —
and cannot, by construction, do anything it lacks scope for.

## Inputs needed from the user — ASK BEFORE STARTING
1. AWS credentials (profile or access key) for the project IAM user, with
   rights for Lambda, API Gateway, Secrets Manager, KMS, IAM role creation.
2. Confirm AWS home region (from Phase 01).
3. Approval to store `MNEMOS_DB_URL` in AWS Secrets Manager.

## Sub-phase 4.1 — MCP tool surface
FastMCP app in `services/api` wrapping the engine. The tool surface *is* the
product's API design — write it as carefully as the schema.

| Tool | Scope | Notes |
|---|---|---|
| `remember` | write | requires `source_trust`; defaults to least-trusted |
| `recall` | read | trust-gated; returns score breakdown + `contested` block |
| `recall_as_of` | read | temporal; errors with the real GC boundary |
| `explain` | read | returns a verifiable deposition for an action |
| `blast_radius` | read | preview-only; never mutates |
| `find_skill` | read | trust-gated |
| `learn_skill` | write | agent-authored skills land quarantined |
| `record_action` | write | binds an action to the recalls that caused it |
| `memory_stats` | read | tier counts, trust distribution, consolidation lag |
| `verify_ledger` | read | runs the verifier, returns VALID / first break |
| `where_is` | read | residency of a subject's rows |
| `forget` | **admin** | Warden-executed; requires `confirm=true` + mode |
| `revoke_source` | **admin** | Warden-executed; requires `confirm=true` |
| `set_legal_hold` | **admin** | Warden-executed |

- [ ] Tool descriptions written for *agent* consumption: unambiguous, with
      safe defaults, explicit irreversibility warnings on `forget` and
      `revoke_source`, and a note on each read tool about trust filtering.
      Bad tool descriptions are the most common failure of MCP servers —
      treat this as UX work, and test it by having a fresh Claude Code
      session accomplish a task using only the descriptions.
- [ ] Streamable HTTP transport (works over API Gateway).
- [ ] **The admin tools do not execute in this service.** They validate,
      then invoke the Warden Lambda across an IAM boundary. The API's own
      execution role has no DELETE grant and no KMS delete permission —
      provable by reading the policy. Invariant 1 lives in IAM, not in code
      comments.
**Accept:** a local MCP client (Claude Code) lists all tools and round-trips
remember → recall → record_action → explain against the local DB.

## Sub-phase 4.2 — AuthN/Z
- [ ] Per-tenant API keys `mn_live_...`, hashed at rest (`api_keys` table
      from migration 001); key → tenant resolution middleware sets
      `app.tenant_id`, with RLS as the backstop beneath it. **Test that RLS
      still holds when the middleware is deliberately bypassed** — defense in
      depth is only real if you verify the second layer alone.
- [ ] Key scopes: `read`, `write` (+remember/learn/record_action),
      `admin` (+forget/revoke/hold). Custodian and demos get least-privilege
      keys. The Custodian's key is `write` — it can never forget anything.
- [ ] **Dual control on destructive ops (optional per tenant, on for demos):**
      `forget` and `revoke_source` can require two distinct admin keys within
      a time window. Real erasure in regulated environments is two-person;
      modeling that is a Production Readiness point judges will notice.
- [ ] Rate limit per key (token bucket; documented limits), plus a separate,
      much tighter bucket on admin operations.
- [ ] Every denied call is audited with the reason — denials are security
      telemetry, not noise to discard.
**Accept:** scope matrix tested exhaustively (every tool × every scope);
wrong-scope calls return clean typed errors; dual-control path tested both
ways; RLS-alone test green.

## Sub-phase 4.3 — REST facade (for the console)
- [ ] Thin FastAPI routes mirroring the tools, plus:
      `GET /ledger/{tenant}` (paged, per-shard),
      `GET /checkpoints/{tenant}`,
      `GET /facts/{tenant}` (paged, filterable by trust state),
      `GET /deposition/{action_id}`,
      `GET /residency/{tenant}`,
      `GET /events/stream` (SSE, fed by the Phase 02 changefeed — live facts
      and live revocations for the console).
- [ ] OpenAPI schema at `/openapi.json` (console codegen in Phase 08).
**Accept:** SSE endpoint delivers a fact insert and a revocation within 5s of
the database commit.

## Sub-phase 4.4 — Deploy
- [ ] `infra/api/`: Lambda (container image via uv), API Gateway HTTP API,
      Secrets Manager for the DB URL, least-privilege execution role with an
      explicit `Deny` on KMS `ScheduleKeyDeletion` and on the Warden's
      functions. Write the deny statements even where they're redundant —
      they document intent and they survive future policy drift.
- [ ] Cold-start budget: measure; if p95 cold > 2.5s, enable one provisioned
      concurrency unit for demo week (cost noted in `docs/costs.md`).
- [ ] Smoke suite runs against the deployed endpoint in CI (manual trigger).
**Accept:** public endpoint serves MCP; Claude Code connects from a laptop
using only the README snippet.

## Sub-phase 4.5 — Client snippets
- [ ] README section with three tested, copy-paste snippets: Claude Code,
      Cursor, LangChain/LangGraph (the last using
      `AsyncCockroachDBVectorStore` + the LangGraph checkpointer so the
      integration story is complete).
- [ ] A 20-line "hello memory" example an evaluator can run in 60 seconds.

## Definition of Done
- [ ] Deployed, authenticated, rate-limited MCP endpoint; full scope matrix
      green; three client snippets verified working from a clean machine.
- [ ] The API role's inability to delete is proven by a test that tries.
**Est: 4 days.**
