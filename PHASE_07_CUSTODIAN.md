# Phase 07 — The Custodian (self-maintaining agent)

**Objective:** The Fargate agent that maintains Mnemos's own brain: it runs
official CockroachDB Agent Skills through the Cloud MCP server (read-only),
files structured findings, and feeds operational self-knowledge back into
semantic memory — so `recall("any hot queries this week?")` is answered from
the fabric's memory of its own operations.

The Custodian is the one component in Mnemos that *is* an LLM agent. It is
therefore also the component with the least privilege: it can write memory
and it can never destroy any.

## Inputs needed from the user — ASK BEFORE STARTING
1. CockroachDB Cloud **service account** with read-only cluster access; its
   API credentials.
2. The cluster's **MCP cluster ID** (for the `mcp-cluster-id` header).
3. Approval for ECS/Fargate + ECR resources.
4. Confirmation the `ccloud` CLI can be authenticated with a service account
   (used in 7.5).

## Sub-phase 7.1 — Skills runtime
- [ ] Vendor the official `cockroachdb-skills` repo into the Custodian image
      (pin the commit; document license compliance in `docs/attribution.md`).
- [ ] Skill loader: parse `SKILL.md` + `references/` for the target skills:
      `triaging-live-sql-activity`, `profiling-statement-fingerprints`,
      `reviewing-cluster-health`, `analyzing-range-distribution`, and the
      `cockroachdb-sql` schema-review skill (reused from Phase 02.9, now run
      continuously rather than once).
- [ ] **ADR-011 changed this design.** The Phase 02.1 probe found that
      `crdb_internal` and `system` are restricted on CockroachDB Cloud Basic
      (`Access to crdb_internal and system is restricted`), and the ops skills
      lean heavily on `crdb_internal`. Their raw SQL largely cannot run on our
      cluster.

      So the allowlist is **the Cloud MCP server's purpose-built tools**, not
      SQL strings parsed out of markdown: `show_running_queries`,
      `show_statement`, `explain_query`, `get_table_schema`, `list_tables`,
      `list_databases`. Each skill's `SKILL.md` triage guidance becomes the
      interpretation prompt. The skills supply the expertise; the MCP server
      supplies the safe accessors.

      This is the better design anyway — a vendor-maintained tool surface has
      no SQL-injection surface at all, and it works identically on Basic,
      Standard, and Advanced.
- [ ] Skill diagnostics with no MCP equivalent are **skipped and logged as
      unavailable**, never silently omitted. The count of skipped diagnostics
      appears in each sweep's record; silent partial coverage reads as full
      coverage, which is the failure mode Phase 10.7 exists to prevent.
- [ ] This finding is genuine, specific feedback for the sponsor's optional
      feedback field — record it in `docs/feedback.md` as it happens.
**Accept:** loader test enumerates every skill and the MCP tools it maps to;
a test proves an attempt to call a non-allowlisted tool is rejected before it
reaches the MCP client; a test proves an unavailable diagnostic is reported,
not dropped.

## Sub-phase 7.2 — MCP client (CockroachDB Cloud side)
- [ ] MCP client to `https://cockroachlabs.cloud/mcp` with service-account
      auth + `mcp-cluster-id` scoping. **Read-only asserted at startup**: probe
      the tool list; if any write-capable tool is present and not denied, hard
      fail and refuse to run. Do not trust the mode — verify it.
- [ ] Respect server limits by design: one statement per call, 20s timeout,
      ~25-row SELECT truncation (paginate diagnostics deliberately), 10KiB
      response cap handling, no `crdb_internal`.
- [ ] Every MCP call and its response are logged with the run_id — the
      Custodian's own activity is auditable like everything else.
**Accept:** integration test lists tables and runs one allowlisted diagnostic
against the real cluster, correctly paginating past the 25-row cap.

## Sub-phase 7.3 — The sweep loop
- [ ] A sweep, per skill: run its allowlisted queries via MCP → Bedrock
      (Claude) interprets the results **using the skill's own triage guidance
      as prompt context** → structured findings
      `{severity, summary, evidence, recommendation, skill_id}`.
- [ ] Findings persist to `custodian_findings` (one `run_id` per sweep) via
      the engine using a **write-scoped** key. The Custodian has no admin
      scope; `forget` and `revoke_source` are not in its tool surface at all.
- [ ] Warn/critical findings are also distilled into `semantic_facts` at
      `fact_kind='ops_finding'` with `source_trust='agent'` — meaning they
      enter memory **unverified like any other model output**, and are
      promoted only when a second independent sweep or a metric corroborates
      them. The Custodian does not get to believe itself on the first pass.
      This consistency is the point: our own agent is subject to our own
      trust lattice.
- [ ] Self-referential test: after two sweeps that agree, `recall("any recent
      hot queries?")` returns the Custodian's corroborated finding with
      provenance back to the exact MCP call. **Video Moment #3.**
**Accept:** an end-to-end sweep on the live cluster produces ≥1 plausible
finding; the promotion-on-second-sweep behaviour is tested.

## Sub-phase 7.4 — Custodian-initiated governance *proposals* (not actions)
The Custodian will sometimes notice something that ought to be destroyed — a
table bloating, a tenant's memory drifting, a source that looks poisoned.

- [ ] It may create a `governance_proposal` row: what it recommends, why, and
      the evidence. It may **never** execute one.
- [ ] Proposals surface in the console for human approval, and approval routes
      through the Warden with full dual-control. The audit row records both
      the proposing agent and the approving human.
- [ ] Test: a Custodian attempt to call any Warden endpoint is rejected at the
      IAM layer, not just the application layer.
**Accept:** the IAM rejection test green; a proposal round-trips
proposal → console → human approval → Warden execution → audit row naming both
parties. *"The agent can ask. Only a person can answer."*

## Sub-phase 7.5 — ccloud CLI integration (the fourth CockroachDB tool)
- [ ] The Custodian shells out to `ccloud` with a scoped service account for
      control-plane facts the MCP server cannot provide: cluster inventory,
      region topology (feeding the residency map), backup inventory and
      recency (feeding the retention-honesty story), and audit-log export
      status. JSON output parsed directly — the CLI's consistent noun-verb
      + JSON design is what makes this agent-safe.
- [ ] Backup-recency finding: if the latest backup is older than the tenant's
      declared RPO, that is a critical finding filed into memory. A memory
      system that notices its own backups are stale is a memory system that
      has thought about failure.
- [ ] The service account used here is read-only on the control plane.
      Document its permissions in `docs/security.md`.
**Accept:** a sweep produces at least one finding sourced from `ccloud` and
one from the MCP server, and the console shows which tool produced which.

## Sub-phase 7.6 — Deploy: scheduled + alarm-triggered Fargate
- [ ] ECR image; ECS task definition (0.25 vCPU / 0.5 GB); EventBridge
      schedule every 6h; **and** CloudWatch alarms (API p95 latency,
      consolidation lag from Phase 05, Lambda error spike) also trigger a
      sweep. The dual trigger — scheduled hygiene plus reactive
      investigation — is what an on-call engineer actually does.
- [ ] Task role: least privilege (Secrets Manager read for its two
      credentials, logs write, Bedrock invoke, nothing else). Explicit deny on
      all Warden functions and on KMS deletion. Documented in
      `docs/security.md`.
- [ ] Cost note in `docs/costs.md`: ~10 compute-hours/month.
**Accept:** a scheduled sweep observed end-to-end in CloudWatch; an
alarm-triggered sweep verified by firing the alarm manually.

## Definition of Done
- [ ] Custodian sweeps on schedule and on alarm; findings land in the database
      and in semantic memory under the same trust rules as any other agent
      output; read-only, allowlist, and no-destruction guarantees enforced by
      code *and* IAM *and* tests.
- [ ] Both the Cloud MCP Server and the `ccloud` CLI are demonstrably in use,
      with the console attributing findings to each.
**Est: 5 days.**
