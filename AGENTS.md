# AGENTS.md — Instructions for the executing agent

**Mnemos — Accountable Memory for Agents**, built for the CockroachDB × AWS
"Build with Agentic Memory" hackathon. The build is complete and submitted;
this file is the durable engineering contract for any further work on it —
the invariants that must never regress, the language discipline that keeps
the README honest, and the rules that made `make check` green in the first
place. Read `docs/architecture.md` and `README.md` before touching code.

Mnemos is not a memory store with governance bolted on. Governance is the
product; the memory tiers are the substrate. When a design decision trades
recall cleverness against provability, provability wins every time.

## The five sacred invariants (never violate, never "temporarily" bypass)

1. **No LLM-driven process ever holds DELETE or governance privileges.**
   The Warden (`packages/warden`) is the only component that can destroy or
   change policy, and it contains no model call — not one. The Custodian and
   every Bedrock-touching path uses read- or write-scoped credentials only.
   Enforced by CockroachDB role grants, asserted at service startup, and
   tested in `redteam/`.
2. **Every state-changing memory op appends a hash-chained audit row in the
   same transaction.** Enforced by a database trigger, not by discipline. If
   the audit row cannot be appended, the mutation must not commit.
3. **No fact becomes recallable without provenance to at least one episode.**
   A fact with zero provenance edges is a bug, not a belief.
4. **Memory rows never leave their home region.** Cross-region reads return
   policy-approved derived projections only, and every crossing is logged to
   the ledger with the policy that permitted it.
5. **Erasure is atomic across rows, vectors, and provenance — or it does not
   happen.** Legal hold outranks erasure and must refuse loudly, with the
   hold reference, never silently.

## Language discipline (this matters for judging)

Claim only what a test proves.

- Say **"tamper-evident, transactionally atomic erasure from the live
  keyspace, with MVCC GC and backup retention documented, plus crypto-shred
  for backup-resident copies."** Do NOT say "cryptographically proven
  deletion" — the hash chain proves the deletion was *recorded* and
  unmodified, not that every byte is gone.
- Say **"multi-region residency, demonstrated on a 9-node local cluster with
  simulated localities; cloud deployment is single-region on the free tier."**
  Do NOT claim a globally distributed production deployment we did not run.
- Say **"designed against GDPR Art. 17/44, EU AI Act Art. 12, HIPAA
  §164.312(b), India DPDP §12."** Do NOT say "compliant" or "certified."
- Every performance number in docs carries the hardware, the dataset size,
  and the date it was measured.

Naming the limits of our own claims is a Production Readiness point, not a
weakness. The team that discloses its GC window beats the team that gets
caught not knowing about it.

## When to STOP and ask the user
Never fabricate, guess, or placeholder-commit credentials, connection
strings, API keys, cluster IDs, account/region choices, or brand approvals.
Stop and ask before: any spend beyond agreed budgets, deleting any cloud
resource, force-pushing, opening or merging a pull request against a
third-party repository under the user's identity, or creating a new S3
Object Lock bucket (compliance-mode objects genuinely cannot be deleted
until retention expires — the user must approve knowingly).

## Engineering rules
- Secrets: local `.env` (gitignored) + AWS Secrets Manager. `.env.example`
  current. gitleaks in CI and before every push.
- Every CockroachDB transaction goes through the retry wrapper (40001 →
  bounded exponential backoff). No naked transactions. Ever.
- Tests land with code; `make check` must stay green — that is the whole
  quality bar, not a suggestion.
- **Verify DDL against the real cluster version before relying on it.**
  `VECTOR INDEX` (C-SPANN), `TSVECTOR`, `REGIONAL BY ROW`, RLS policy syntax,
  and trigger/UDF support all drift across 25.x/26.x. Do not trust memory or
  this document over the live cluster — record findings in ADR-006.
- Respect Cloud MCP server limits in the Custodian: one statement per call,
  20s timeout, ~25-row SELECT truncation (paginate), 10KiB response cap, no
  `crdb_internal`. The Custodian may only execute allowlisted SQL extracted
  from the official skills' `references/` files.
- Anything an LLM writes into memory is **untrusted input**. It lands at
  `trust='unverified'` and is not recallable until the corroboration gate in
  Phase 05 promotes it. This is invariant 3's teeth.
- Brand tokens (`brand/tokens.css`) are law after Phase 01: synapse teal =
  reading memory, ember amber = writing/consolidating, signal red =
  destruction, umbra violet = doubt (quarantined, contested, unverified).
  Never introduce new colors or fonts.
- Write ADRs in `docs/decisions.md` for any deviation from the architecture
  or invariants documented here, and get user approval for the deviation
  first.
- Conventional commits; small, reviewable PRs.

## Reference documentation (consult these; do not rely on memory)
- Hackathon brief + rules: https://cockroachdb-aws.devpost.com/
- CockroachDB Cloud console: https://cockroachlabs.cloud
- Cloud MCP Server — auth, `mcp-cluster-id` scoping, tool list, limits:
  https://www.cockroachlabs.com/docs/cockroachcloud/connect-to-the-cockroachdb-cloud-mcp-server
- CockroachDB and AI (vector type, C-SPANN, MCP, skills, ccloud):
  https://www.cockroachlabs.com/docs/v26.2/cockroachdb-and-ai
- Multi-region: `REGIONAL BY ROW`, survival goals, locality-optimized search
- `AS OF SYSTEM TIME` and MVCC GC (`gc.ttlseconds`) — the temporal recall
  window is bounded by GC; read this before promising time travel
- Change data capture (CHANGEFEED) — revocation propagation, console live feed
- Row-Level TTL — episodic decay
- Agent Skills repo (five skills vendor-pinned in `services/custodian`; two
  contributed upstream, PRs [#27](https://github.com/cockroachlabs/cockroachdb-skills/pull/27)
  and [#28](https://github.com/cockroachlabs/cockroachdb-skills/pull/28)):
  https://github.com/cockroachlabs/cockroachdb-skills
- LangChain integration (AsyncCockroachDBVectorStore, HybridSearchConfig,
  chat history, LangGraph checkpointer + TTL):
  https://docs.langchain.com/oss/python/integrations/providers/cockroachdb
- AWS: Bedrock (Claude + Titan Embed V2, 1024-dim), KMS envelope encryption
  and key deletion semantics, S3 Object Lock compliance mode, Step Functions
- Community Slack for blockers: https://www.cockroachlabs.com/join-community/

## The standard any change must still meet
A stranger on a fresh machine, following only `README.md`, must be able to
reach the live console, run `make smoke` against the deployed instance, and
independently verify a deposition against the S3-anchored Merkle root. Any
change that breaks that path — a stale link, a claim the code no longer
backs, a broken quickstart command — is a regression, full stop, judged by
the same standard `PHASE_12_SUBMISSION.md`'s judge-simulation step used
before that file was retired from the repo.

**Never cut, weaken, or leave undocumented:** the Warden's erasure +
attestation path, blast-radius revocation, temporal recall, or the
Custodian's read-only guarantee. Those four are the entire differentiation
this project has over a plain vector store.
