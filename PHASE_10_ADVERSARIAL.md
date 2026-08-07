# Phase 10 — The Adversarial Phase (attack our own memory)

**Objective:** `redteam/` — a real, runnable attack suite against Mnemos,
executed by us, with the results published *including the failures*. Every
security claim this project makes gets an adversary assigned to break it.

Most hackathon projects assert that they are secure. This phase is the
difference between asserting and demonstrating, and it is the cheapest
Production Readiness points available — provided we publish honestly.

**Scope discipline:** every attack in this phase targets infrastructure we own
and deploy, in our own AWS account and our own CockroachDB cluster, against
dedicated `redteam-*` tenants. Nothing here touches third-party systems.

## Inputs needed from the user
1. Approval to run load- and fault-injection against our own deployed stack
   (may briefly degrade the demo endpoint — schedule it away from filming).
2. Approval to publish the findings, including the ones we failed, in
   `docs/redteam.md`.

## Sub-phase 10.1 — Attack class 1: Memory poisoning
- [ ] `redteam/poison/` — a corpus of injection payloads placed in episode
      content: direct instruction override, delimiter escape, encoded
      payloads (base64, unicode confusables, zero-width), multi-turn
      split-payload injection assembled across several episodes, and
      injection aimed at the *consolidation* prompt rather than the agent
      (the underrated one — the distiller is the highest-value target because
      its output is trusted downstream).
- [ ] Success criteria for the attacker: get a fact to `trusted` state, get a
      skill to executable, or get any Warden operation invoked.
- [ ] Measure and publish: payloads attempted, blocked at the prompt layer,
      blocked at the corroboration gate, and **succeeded**. Report the
      collusion threshold honestly — how many independent-looking sources an
      attacker must control to promote a fact. If that number is 2, say 2.
- [ ] For every success: fix it, or document why it is accepted risk with the
      compensating control (blast-radius revocation is our containment when
      prevention fails — that is a legitimate answer, stated plainly).
**Accept:** the suite runs in CI as a regression gate; the results table is in
`docs/redteam.md` with real numbers.

## Sub-phase 10.2 — Attack class 2: Cross-tenant exfiltration
- [ ] Attempt tenant-boundary crossing through every surface: crafted SQL via
      the API, vector similarity search designed to surface neighbors from
      another tenant (the subtle one — an unscoped ANN index will happily
      return them), subquery/CTE/RETURNING tricks, error-message oracles,
      timing side channels on recall latency, and MCP tool arguments carrying
      another tenant's IDs.
- [ ] Attempt the same with the RLS middleware deliberately disabled, to prove
      the database-layer policy alone holds.
- [ ] Attempt embedding-inversion leakage: given API access to tenant A, can
      any tenant B content be inferred from returned scores? Document the
      finding either way — this is a real and under-discussed risk in every
      vector-backed memory system.
**Accept:** zero successful crossings; the vector-index scoping test is
explicitly called out in the results table because it is the one most systems
get wrong.

## Sub-phase 10.3 — Attack class 3: Ledger tampering
- [ ] As a database superuser (the strongest realistic insider), attempt:
      single-field edit, row deletion, full shard rewrite with recomputed
      internal hashes, checkpoint forgery, and backdating.
- [ ] Prove the detection boundary precisely: an internally-consistent shard
      rewrite is caught **only** by the S3-anchored checkpoint, so state
      exactly how long an attacker's window is (up to one checkpoint epoch)
      and what shortening the epoch costs. Do not claim tamper-*proof*; claim
      tamper-*evident within one epoch*, and publish the epoch.
- [ ] Attempt to delete or alter the S3 anchor with account-root credentials —
      and show Object Lock compliance mode refusing. Film it.
**Accept:** every tamper attempt detected; the detection-window analysis
published with its assumptions.

## Sub-phase 10.4 — Attack class 4: Residency violation
- [ ] Attempt to move a subject's raw content across a boundary via: direct
      API read from a foreign region, consolidation batching (make a
      cross-region batch and see whether facts land in the wrong region),
      recall projections that over-share, changefeed subscription from a
      foreign region, backup/restore into another region, and the console.
- [ ] Attempt to *change* a residency policy without admin scope, and to
      change it retroactively so past crossings appear legitimate.
**Accept:** zero raw-content crossings; every attempted crossing logged;
retroactive policy edits impossible and audited.

## Sub-phase 10.5 — Attack class 5: Erasure evasion and abuse
- [ ] Prove data is truly gone after `forget`: raw vector similarity against a
      pre-computed embedding of the deleted text, `AS OF SYSTEM TIME` reads
      inside the GC window (this one **will** find it — that is expected and
      must be documented, with legal hold / extended GC as the deliberate
      mechanism), backup restore, and changefeed replay.
- [ ] Prove `shred` closes the backup path: restore a backup of a shredded
      tenant and show unreadable ciphertext.
- [ ] The abuse direction, which matters just as much: can an attacker with a
      stolen `write` key *cause* destruction? Can they trigger revocation of
      legitimate memory (a denial-of-memory attack)? Can they place a
      spurious legal hold to block a lawful erasure? Test each; ensure each
      requires admin scope and dual control, and each is loudly audited.
**Accept:** the honest erasure matrix published: for each mode, exactly which
copies of the data survive, for how long, and how to close each path.

## Sub-phase 10.6 — Attack class 6: Availability and correctness under stress
- [ ] Concurrency assault: hammer the same subject key from 200 clients to
      force 40001 storms; prove the retry wrapper holds and the chain remains
      gapless and correctly ordered per shard.
- [ ] Interleave `forget` with concurrent `remember` and `recall` on the same
      subject and prove no torn state, no orphaned vector, no phantom recall.
      This is the hardest correctness property in the system; write the test
      that would catch us being wrong.
- [ ] `docker kill` a node on the 9-node rig mid-write; assert zero lost
      acknowledged writes and continued availability under `SURVIVE REGION
      FAILURE`.
- [ ] Kill the consolidation Step Function mid-execution; assert no
      corruption and clean resumption.
- [ ] Throttle Bedrock to zero; assert `remember` and `recall` are entirely
      unaffected — **memory intake survives total AI outage**, because the
      write path does no AI work. That property is a deliberate design
      decision from Phase 03; here it gets proven.
**Accept:** all five green; the node-kill and Bedrock-outage clips are cut for
the video.

## Sub-phase 10.7 — Publish
- [ ] `docs/redteam.md`: methodology, every attack, the result, and the fixes.
      A "what we did not test" section — because the omissions a team names
      are the ones you can trust them on.
- [ ] `docs/limits.md`: the single page a skeptical engineer should read.
      Embedding leakage. MVCC GC windows. Backup retention. The collusion
      threshold. The checkpoint detection window. Free-tier single-region
      reality vs the 9-node rig. Written plainly, no hedging.
- [ ] The regression subset runs in CI on every PR.

## Definition of Done
- [ ] Six attack classes executed, results published including failures.
- [ ] Every failure either fixed or documented with a compensating control.
- [ ] `docs/limits.md` exists and is honest enough to be uncomfortable.
**Est: 5 days. This phase is a differentiator, not overhead — no team that
skips it can claim what we claim.**
