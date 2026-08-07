# Phase 05 — Sleep Cycle (consolidation, belief revision, trust promotion)

**Objective:** The intelligence layer: Step Functions–orchestrated Lambdas that
distill episodes into facts via Bedrock, revise beliefs when evidence
conflicts, promote facts through the trust lattice only on independent
corroboration, and decay what goes unused.

This phase is where a memory system usually becomes gullible. Ours does not:
**everything an LLM writes enters memory as untrusted and must earn its way in.**

## Inputs needed from the user — ASK BEFORE STARTING
1. Bedrock model access enabled in the home region for an Anthropic Claude
   model (distillation) and `amazon.titan-embed-text-v2:0` (1024-dim). User
   clicks "request model access"; agent verifies with a test invoke.
2. Approve a monthly Bedrock budget guardrail (suggest $25 hard alarm).

## Sub-phase 5.1 — Distillation prompt engineering (do this FIRST, offline)
- [ ] `services/sleep-cycle/prompts/distill.md`: system prompt taking a
      session's episodes, returning STRICT JSON:
      `[{fact_text, subject_key, fact_kind, confidence, source_event_ids[],
      contradicts_hint}]`.
- [ ] Rules encoded in the prompt: durable facts only (no chit-chat),
      subject_key taxonomy (`user:<id>`, `patient:<id>`, `system:<name>`,
      `domain:<topic>`), calibration guidance for `confidence`, max facts per
      session, and an explicit instruction that **every fact must cite at
      least one source_event_id** (invariant 3 enforced in the prompt *and*
      again in code — the code check is what actually holds).
- [ ] **Injection resistance in the prompt itself:** episode content is
      wrapped in delimiters and the system prompt states that content is data,
      never instruction. Then assume the prompt fails anyway — the corroboration
      gate in 5.4 is the real defense, and Phase 10 will attack both layers.
- [ ] Golden-set eval: 15 hand-written sessions in `tests/golden/` with
      expected facts (including 3 adversarial sessions containing embedded
      injection attempts that must NOT produce facts). Scorer measures
      extraction precision/recall and injection-resistance separately.
      Iterate the prompt until ≥85% extraction agreement and **100% on the
      injection cases** (a single failure here is a shipping blocker).
**Accept:** golden eval ≥85%; injection subset 100%; malformed-JSON rate <2%
over 50 runs with one repair-retry allowed.

## Sub-phase 5.2 — Consolidation Lambda
- [ ] Batch loop: unconsolidated episodes grouped by (tenant, session), oldest
      first, capped per run (`MAX_SESSIONS_PER_RUN`).
- [ ] **Residency-aware batching:** episodes are processed by a worker in
      their home region and derived facts are written to the same region.
      Consolidation must never be the hole through which data leaves a
      jurisdiction (invariant 4). Cross-region batches are split, not merged.
- [ ] Bedrock invoke (Claude, low temperature) → parse/repair → Titan
      embeddings (batched) → write facts at `trust='unverified'`.
- [ ] Provenance edges written in the same txn as their fact; a fact whose
      cited `source_event_ids` don't resolve is **dropped, logged, and
      counted** — never written with a dangling edge.
- [ ] Single serializable txn per session: facts + provenance + mark episodes
      consolidated + audit row `op='consolidate'`. Crash-safety inherited: a
      died run leaves episodes unconsolidated; the next run retries.
- [ ] Cost discipline: token counts logged per run in the `docs/costs.md`
      format; Haiku-class model default, larger model behind an env flag for
      demo takes.
**Accept:** run against the seeded DB — correct facts appear at `unverified`,
re-run is a no-op, `kill -9` mid-run corrupts nothing, and a cross-region
tenant's facts stay in their region.

## Sub-phase 5.3 — Belief revision (near-duplicate, reinforce, supersede, contest)
Four outcomes when a new fact meets an existing one. Most systems have two.

- [ ] C-SPANN top-k against existing facts for the same subject, then classify:
      - **cosine ≥ 0.92 and non-contradictory** → *reinforce*: strength += w,
        provenance appended, corroboration counter incremented (feeds 5.4).
      - **contradictory, new evidence stronger** → *supersede*: insert new,
        set old `superseded_by`, both retained forever (never delete —
        superseded history is exactly what a deposition needs).
      - **contradictory, evidence comparable** → *contest*: both marked
        `contested`, linked to each other. `recall` surfaces both with their
        evidence rather than picking a winner.
      - **novel** → insert.
- [ ] The contradiction judgment is a cheap, tightly-scoped LLM call
      (yes/no/unclear + one-line reason) whose output is *structured data, not
      an action* — it can influence a state field but can never trigger a
      delete. Its reason string is stored as evidence in the audit row.
- [ ] Test: feed a session that contradicts a seeded fact with weak evidence →
      `contested`; feed one with strong corroborated evidence → `superseded`;
      assert the old row still exists and `recall_as_of` still finds it.
**Accept:** all four outcomes reproduced by tests; no path deletes a row.

## Sub-phase 5.4 — The corroboration gate (pillar III, write side)
The single mechanism that makes memory poisoning hard.

- [ ] Promotion rules, configurable per tenant, defaulting to strict:
      - `system` or `operator` source trust → promoted to `trusted` directly.
      - `agent` or `external` source trust → stays `unverified`, and becomes
        `corroborated` only when **two provenance edges from independent
        sources** support it. "Independent" means different session AND
        different source_trust origin — two facts from the same poisoned
        session do not corroborate each other. Getting this definition right
        is the entire defense; write it down in `docs/trust.md` and test it.
      - Facts that fail to corroborate within a TTL window decay toward
        `quarantined` rather than lingering as ambient unverified noise.
- [ ] Promotion and demotion are audited state transitions (`op='promote'` /
      `op='demote'`) with the evidence that justified them.
- [ ] Test: a single malicious episode generating five mutually-supporting
      facts must NOT self-corroborate into `trusted`. This test is the
      product's security thesis in fifteen lines.
**Accept:** the self-corroboration test green; promotion path green with
genuinely independent sources.

## Sub-phase 5.5 — Decay Lambda
- [ ] Weekly: `strength *= exp(-λ × weeks_idle)` for facts unrecalled ≥14d;
      floor at 0.1. **Decay never deletes** — only the Warden deletes. Audit
      row `op='decay'` with counts.
- [ ] Episodic tier decays by Row-Level TTL, except rows under legal hold or
      inside an extended-GC audit window (Phase 06 sets those).
**Accept:** frozen-time unit test shows the correct curve; a held subject's
episodes survive a TTL pass that removes its unheld neighbours.

## Sub-phase 5.6 — Orchestration & deploy
- [ ] **AWS Step Functions** state machine for the nightly cycle: gather →
      distill (map state, parallel per session) → embed → revise → promote →
      checkpoint. Step Functions gives per-stage retry, visible execution
      history, and a screenshot that reads as production infrastructure. The
      hourly light run is a direct Lambda invoke.
- [ ] EventBridge: hourly light run (cap 5 sessions), nightly full cycle,
      weekly decay, hourly Merkle checkpoint.
- [ ] CloudWatch: error alarm, consolidation-lag alarm, Bedrock cost alarm.
      The lag alarm also wakes the Custodian in Phase 07 (dual trigger).
**Accept:** two consecutive scheduled cycles observed in Step Functions
execution history against the cloud cluster; facts visible via `recall`.

## Sub-phase 5.7 — The demo beat (rehearsal)
- [ ] `demos/sleep_demo.sh`: converse via MCP → trigger consolidation → recall
      shows distilled knowledge with provenance IDs, trust states, and score
      breakdowns in a rich CLI. Then show one fact sitting at `unverified` in
      umbra violet, add an independent corroborating source, and watch it
      promote to `trusted` live. **That promotion moment is Video Moment #1**
      — it is more interesting than "the LLM summarized things" and it is
      unique to us.

## Definition of Done
- [ ] Golden eval ≥85% with 100% injection resistance; scheduled cycles green;
      all four belief-revision outcomes proven by tests; corroboration gate
      proven against self-corroboration; costs logged.
- [ ] No code path in this service holds DELETE. Verified by the CI grep and
      by the pipeline role's grants.
**Est: 6 days. Prompt quality and the corroboration gate are the ceiling of
the whole product — spend the time in 5.1 and 5.4.**
