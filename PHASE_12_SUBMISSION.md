# Phase 12 — Submission & Launch

**Objective:** Convert the build into a winning submission: video, Devpost,
final verification. Budget 3 full days — never submit on deadline day.

## Inputs needed from the user — ASK BEFORE STARTING
1. Devpost account logged in; team members added.
2. YouTube or Vimeo account for the video upload (public).
3. Who narrates the video (user's own voice strongly preferred over TTS —
   judges watch dozens of these and a real voice registers as a real team).

## Sub-phase 12.1 — The 3-minute video (hard cap; script for 2:50)

The video's job is not to list features. It is to make a judge feel the three
questions nobody can answer, then answer them on screen. Every second of
screen time is a real system doing a real thing — no slideware after 0:25.

Structure (seconds):

- **0:00–0:18 — The hook.** "Agents don't fail when they're wrong. They fail
  when they forget — or when they remember what they never should have."
  Then the three questions, on screen, one per beat: *Where does this memory
  legally live? What did the agent believe when it acted? A poisoned fact
  entered six weeks ago — what did it touch?*

- **0:18–0:35 — Mnemos in one diagram.** Four planes over one CockroachDB
  cluster. Say the line that frames everything: **"The only component that
  can destroy memory has no model in it."**

- **0:35–1:05 — Moment #1: memory that earns trust.** Converse → consolidate →
  a fact appears at `unverified` in violet → an independent source arrives →
  it promotes to trusted, live, in the console. "Everything our AI writes is
  untrusted until something independent agrees."

- **1:05–1:35 — Moment #2: erasure with proof, and the refusal.** Clinic
  framing. Forget flow in one take: preview → confirm → chain re-verified →
  anchor re-verified → recall empty. Then five seconds on the legal hold
  refusing a second erasure, citing the matter reference. The refusal is the
  more sophisticated beat — do not cut it for time.

- **1:35–2:10 — Moment #3: blast radius.** The poisoned runbook. Blast-radius
  graph with real counts: 1 source → 4 facts → 1 skill → 11 recalls → 3
  actions. One `revoke_source`, one transaction, the graph turns violet in
  real time. Then the deposition: *"this decision was influenced by
  subsequently-revoked memory."* Say it plainly: **"One transaction. Because
  the vectors, the provenance, and the audit log are all in the same
  database."**

- **2:10–2:30 — Moment #4: the Custodian, and honesty.** Official CockroachDB
  Agent Skills running over the Cloud MCP Server; `ccloud` reporting backup
  recency; findings entering memory as unverified like everything else; then
  `recall("any hot queries lately?")` answering from the fabric's memory of
  its own operations. Flash: node kill on the 9-node rig, zero lost writes;
  Bedrock throttled to zero, writes unaffected.

- **2:30–2:50 — Close.** Four CockroachDB tools and eight AWS services on
  screen. Red-team results table — *including the row where we failed and what
  we did about it.* Two upstream skill PRs. Repo + live console + judge tenant.

- [ ] Script written → user records narration → edit → **captions** (judges
      often watch muted) → upload public → link tested logged-out and on
      mobile.
- [ ] Every number shown on screen is real and reproducible. If a take
      requires staging, say so in the description rather than letting a judge
      discover it.

## Sub-phase 12.2 — Devpost entry
- [ ] Required fields: public repo URL (Apache-2.0 visible in About), demo URL
      (console + judge tenant link that needs no key), video URL.
- [ ] **"Which CockroachDB tools & how"** — all four, with what the agent
      actually did:
      - *Distributed Vector Indexing (C-SPANN)*: prefix-scoped by `tenant_id`
        so isolation lives inside the index; hybrid vector + full-text recall
        with RRF; vectors die transactionally with their rows, which is the
        entire erasure guarantee.
      - *Cloud MCP Server*: the Custodian connects read-only with
        `mcp-cluster-id` scoping, runs only allowlisted SQL extracted from
        official skills, paginates around the 25-row / 10KiB limits.
      - *Agent Skills*: five consumed (four ops skills + `cockroachdb-sql` for
        continuous schema review) and **two contributed upstream** into the
        empty integrations domain.
      - *ccloud CLI*: control-plane facts the MCP server cannot give —
        cluster inventory, region topology for the residency map, and backup
        recency, which becomes a critical finding when it exceeds RPO.
      Requirement is two. We used four.
- [ ] **"Which AWS services & how"** — Bedrock (distillation, contradiction
      judging, Titan embeddings), Lambda (MCP API, consolidation, decay,
      Warden), Step Functions (sleep-cycle orchestration), ECS Fargate
      (Custodian), EventBridge (all scheduling + alarm triggers), S3 (artifacts
      **and Object Lock compliance mode for immutable Merkle anchoring**),
      KMS (per-tenant envelope encryption and crypto-shredding), CloudWatch
      (alarms that wake the Custodian), API Gateway, Secrets Manager, Amplify
      (console hosting).
- [ ] Architectural diagram attached (optional field — attach it).
- [ ] Optional feedback field: submit our genuine, specific notes — MCP
      pagination ergonomics, `ccloud` JSON shapes, C-SPANN build observations,
      `AS OF SYSTEM TIME` + GC interaction for audit workloads. Sponsors read
      this section, and specific beats flattering.

## Sub-phase 12.3 — Judge simulation (the step everyone skips)
- [ ] Fresh machine, fresh judge persona, README only. Clock the quickstart.
      Click every link. Run every demo. Verify a deposition offline. Fetch the
      S3 anchor and verify our chain with `independent_verify.py`. Fix every
      point of friction found, however small.
- [ ] Second pass by the user personally, on a different machine.
- [ ] Ask someone outside the project to watch the video once and then explain
      what Mnemos does. If they say "it stores agent memory," the video has
      failed and needs a recut — they should say something closer to *"it
      proves what an agent remembered and lets you delete or revoke it."*

## Sub-phase 12.4 — Freeze & submit
- [ ] Tag `v1.0.0`; deployment health-check script green across API, console,
      Custodian, sleep cycle, and attestation.
- [ ] Bedrock quota and API rate limits sized for judge traffic; judge tenant
      read-only and rate-limited so it cannot be exhausted.
- [ ] Cost alarms armed so a judging spike cannot produce a surprise bill.
- [ ] Submit **≥24h before the Aug 19 deadline**; confirmation screenshot saved.

## Sub-phase 12.5 — Post-submit guard
- [ ] No breaking changes to `main` after submission (judges may pull at any
      time). Hotfixes only, tagged, with the tagged v1.0.0 always reachable.
- [ ] Daily health check of the demo URL and the judge tenant until winners
      are announced.
- [ ] Watch the two upstream PRs and respond to maintainer review promptly —
      a merged PR before judging closes is worth more than an open one.

## Definition of Done
- [ ] Submitted 24h+ early; judge-simulation friction list empty; demo stack
      monitored; upstream PRs live and responsive.
**Est: 3 days.**
