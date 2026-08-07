# Phase 11 — Hardening, Scale, Observability & Open-Source Contribution

**Objective:** Turn "works and survives attack" into "would survive
production," and land upstream contributions no other team will have.

## Inputs needed from the user
1. GitHub account able to fork `cockroachlabs/cockroachdb-skills` and open
   PRs (agent prepares everything; user clicks submit if 2FA gates it).
2. Approval of the load-test spend envelope (bulk vector load + sustained
   traffic against the cloud cluster).

## Sub-phase 11.1 — Security pass
- [ ] Secrets audit: full repo history scan (gitleaks) — zero findings.
- [ ] IAM review: every role least-privilege, documented in `docs/security.md`
      as a table (principal → permissions → why → what it is explicitly
      denied). The denies matter as much as the grants.
- [ ] Dependency audit: pip-audit / npm audit clean or with documented,
      justified exceptions. SBOM committed.
- [ ] Prompt-injection surface documented per component (consolidation
      distiller, contradiction judge, Custodian interpreter, demo agents) with
      the mitigation for each and a pointer to the Phase 10 test that proves it.
- [ ] **The five invariants each get a dedicated, named test** in
      `tests/invariants/`, each with a comment explaining what business or
      legal property it protects. These tests are cited by name in the README
      — a judge should be able to run `make invariants` and watch all five
      hold in under a minute.
**Accept:** `make invariants` green; security doc complete.

## Sub-phase 11.2 — Resilience demonstration
- [ ] `demos/resilience.sh` — one command, narratable, running the Phase 10.6
      scenarios as a story rather than a test log: node kill on the 9-node
      rig during live writes (zero lost acknowledged writes), Step Function
      killed mid-consolidation (no corruption, clean resume), Bedrock
      throttled to zero (writes and recalls entirely unaffected).
- [ ] Recovery drill: restore from a `ccloud`-listed backup into a scratch
      cluster, run `mnemos-verify` and `mnemos-attest` against the restored
      data, and confirm the ledger still validates against the S3 anchor.
      **A backup you have not verified is a rumor.** Document RTO/RPO measured,
      not assumed.
**Accept:** both scripts run clean; recovery drill documented with real timings.

## Sub-phase 11.3 — Load & scale evidence
- [ ] k6/Locust profile: sustained `remember` + `recall` + `record_action` mix
      against the deployed API. Publish p50/p95/p99 and error rate in
      `docs/scale.md`, each number carrying hardware, dataset size, and date.
- [ ] **Shard-scaling curve:** throughput vs audit-chain shard count (1, 2, 4,
      8, 16). This chart is the evidence that we *solved* the ledger
      serialization bottleneck rather than confessing it — publish it, and
      publish where it stops scaling and why.
- [ ] Vector scale probe: bulk-load 1M synthetic facts into one tenant;
      publish recall latency at 10k / 100k / 1M with C-SPANN, plus index build
      time. If it degrades, publish that too and explain the mitigation.
- [ ] Blast-radius scaling: contamination-closure latency at 10k / 100k / 1M
      facts. This is our most novel query; its cost curve is genuinely
      interesting and nobody else can publish one.
- [ ] Cost model in `docs/costs.md`: dollars per million memories written,
      consolidated, and recalled — the number an engineering manager actually
      needs before adopting anything.
**Accept:** three charts committed with reproducible scripts in `bench/`.

## Sub-phase 11.4 — Observability
- [ ] Structured logs everywhere with `run_id` / `session_id` / `trace_id`
      correlation (established in Phase 03, verified end-to-end here).
- [ ] CloudWatch dashboard JSON committed in `infra/observability/`:
      write throughput, recall latency, consolidation lag, **trust
      distribution over time** (the leading indicator of a poisoning attempt —
      an unexplained spike in `unverified` volume is the alarm nobody else has
      thought to build), chain height per shard, checkpoint recency, region
      crossings, Warden operations (should be rare and every one interesting).
- [ ] Alarms on: consolidation lag, checkpoint staleness, Bedrock cost, error
      rate, and **any Warden operation** — destruction should always page
      someone.
- [ ] `docs/runbook.md`: the eight most likely failures and their fixes —
      consolidation stuck, checkpoint failing, MCP auth expiry, Bedrock
      throttling, KMS key state, region unavailable, chain verification
      failure, suspected poisoning. Each with detection, diagnosis, and
      remediation. This document is itself rubric evidence.
**Accept:** dashboard renders with live data; a runbook entry is followed
successfully by someone who did not write it.

## Sub-phase 11.5 — Upstream contributions (two PRs)
The `cockroachdb-integrations-and-ecosystem` domain is empty. We fill it with
skills distilled from a real build, not from documentation.

- [ ] **PR 1 — `designing-agentic-memory-schemas`**: SKILL.md following repo
      conventions exactly (frontmatter, guardrails) + `references/`:
      idempotent event tables, provenance graphs with cascade semantics,
      Row-Level TTL decay, prefix-scoped vector indexes for tenant isolation,
      and sharded hash chains with Merkle checkpoints (with the scaling data
      from 11.3 as justification — a skill backed by a benchmark is worth
      more than a skill backed by an opinion).
- [ ] **PR 2 — `auditing-agent-memory-with-as-of-system-time`**: temporal
      recall as a reviewable pattern — reconstructing agent state at a past
      instant, GC window management, extending `gc.ttlseconds` for retention
      obligations, and the failure modes (silently reading `now()` is the trap).
- [ ] Both validated against the repo's own contribution checks. Link both in
      our README; a pending PR is fine — the artifact is the point.
- [ ] Also file genuine feedback for the sponsor's optional feedback field as
      we go: MCP 25-row/10KiB pagination ergonomics, `ccloud` JSON shape notes,
      C-SPANN index build observations, anything that cost us an hour. Real,
      specific, non-flattering feedback is read by the people who build the
      product.
**Accept:** both PRs open and passing the skills repo's checks.

## Sub-phase 11.6 — Docs freeze
- [ ] README final structure: pitch → the three questions nobody can answer →
      architecture diagram → 3-minute quickstart → judge tenant link → the
      four CockroachDB tools and what the agent did with each → AWS services
      and what each does → the five invariants with their test names →
      security + red-team results → limits → scale numbers → the three demos →
      upstream PRs.
- [ ] Every `docs/` page cross-linked; stale content deleted; every claim in
      the README traceable to a test, a chart, or a doc.
- [ ] `docs/architecture.md` final diagrams exported as SVG (readable at
      GitHub's rendered width — test it, most architecture diagrams are not).

## Definition of Done
- [ ] Security, resilience, load, and observability all have artifacts —
      documents, scripts, charts, real numbers — not claims.
- [ ] Two upstream PRs open.
- [ ] `make invariants` green; every README claim traceable.
**Est: 6 days.**
