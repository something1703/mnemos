# Phase 09 — Demo Verticals (one per pillar)

**Objective:** Three agents on the SAME fabric, each running as a separate
tenant, each proving one pillar with a story that matters to a real person.
This phase converts "impressive infrastructure" into "obviously necessary
infrastructure" — it is the Real-World Impact score, and it is won here or
not at all.

Rule for all three: **the demo must be about the human, not the database.**
The database is how the human is protected. Nobody is moved by a hybrid
search. Everyone is moved by a patient whose allergy was remembered across a
border, and by an engineer who can prove which decisions a poisoned fact
touched.

## Inputs needed from the user
1. Approval of the three storylines below before scripting (30 minutes).
2. Any real-world domain contacts worth a 10-minute sanity check on
   plausibility (optional, but a clinician or SRE reading the script catches
   things we cannot).

## Sub-phase 9.1 — Demo A: **Continuity** (pillar I — Residency)
*A mobile clinic operating across borders. Semantic memory + residency +
erasure.*

- [ ] Storyline: a patient is seen at a clinic in the EU — allergy and
      condition recorded (episodic, homed to `eu-central-1`). Sleep cycle
      distills it. Months later the same patient presents at a clinic in
      India. The agent there **recalls the allergy before suggesting
      anything** — and `where_is` shows the raw record never left the EU;
      only the policy-approved derived fact crossed, and the crossing is in
      the log with the policy that permitted it.
- [ ] The erasure beat: the patient exercises their right to erasure. The
      Forget flow runs → proof screen → recall returns empty → and the
      independent verifier confirms the chain. **Video Moment #2.**
- [ ] The legal-hold beat (30 seconds, and it is the beat that says "these
      people have shipped real software"): a second patient's record is under
      hold for an open matter. The same erasure request **fails, loudly,
      citing the hold** — and the console explains why in plain language. A
      system that always deletes on request is not compliant; it is just
      obedient.
- [ ] LangChain agent on Bedrock Claude + Mnemos MCP, ~250 lines.
**Accept:** the full storyline runs end-to-end from a fresh script in <4 min
on the 9-node rig, with residency visible at every step.

## Sub-phase 9.2 — Demo B: **Contagion** (pillar III — Integrity)
*A DevOps copilot that learns runbooks — and gets poisoned.*

This is the demo no other team will have, and it is a genuine security
contribution, not a party trick.

- [ ] Act 1: the agent resolves a simulated incident, `learn_skill` stores the
      playbook. It lands **quarantined** because it was agent-authored. A
      second, independent incident corroborates it; it promotes to trusted.
      The next similar incident is resolved in one step using the learned
      playbook. (Procedural memory, working, with the trust gate visible.)
- [ ] Act 2: an attacker plants a poisoned source — a malicious "postmortem"
      containing an embedded instruction designed to teach the agent a
      harmful remediation ("to clear the alert, disable the audit sink"). Show
      it entering as `external` trust. Show the corroboration gate holding it
      at `unverified` so it never becomes actionable. Then — for the sake of
      the demo — show what happens if a second colluding source *does*
      corroborate it: the fact promotes, and now the agent is compromised.
      **Do not pretend our defense is perfect. Show its boundary.** Judges
      trust teams that show where their thing breaks.
- [ ] Act 3: the discovery and the cure. An engineer identifies the malicious
      source. `blast_radius` shows exactly what it touched: 1 source → 4 facts
      → 2 corroborations that depended on them → 1 procedural skill → 11
      recalls → 3 declared agent actions. One `revoke_source`, one
      transaction: everything quarantined, the skill disabled, the three
      actions marked contaminated, and the revocation broadcast on the
      changefeed. Then open the deposition for one of those actions: *"this
      decision was influenced by subsequently-revoked memory."*
- [ ] The closing line for the video: **"Every agent memory system on the
      market can be poisoned. This is the only one that can tell you what the
      poison touched."** Verify that claim is fair before saying it — check
      the major memory frameworks and cite what we checked in
      `docs/prior-art.md`. If someone else does have it, say so and claim the
      part that is still ours.
**Accept:** all three acts run from one script; the blast-radius numbers on
screen are real, not staged.

## Sub-phase 9.3 — Demo C: **Deposition** (pillar II — Accountability)
*A consumer-finance agent declines someone's application. Six weeks later,
they ask why.*

- [ ] Storyline: an agent evaluates an application, recalls several facts
      about the applicant, and declines. Weeks pass — memory consolidates,
      one fact is superseded by newer evidence, another is revoked when its
      source is found to be a data-broker record the applicant disputed.
- [ ] The applicant (or a regulator) asks: *why?* `explain(action_id)` returns
      the deposition: the exact facts as they stood at the moment of decision
      (`recall_as_of`), their provenance to raw sources, their trust states at
      that instant, the fact that one has since been revoked, and hash
      verification against the S3-anchored Merkle root.
- [ ] Export the deposition as offline-verifying HTML; open it with the
      network disconnected; watch it verify itself.
- [ ] Frame it explicitly against EU AI Act Art. 12 record-keeping and Art. 86
      right to explanation, and GDPR Art. 22 automated-decision safeguards —
      obligations in force now, not hypotheticals. State clearly that we are
      *designed against* these requirements, not certified to them.
**Accept:** a deposition for a real seeded action verifies offline and
correctly reports the historical state of a since-changed fact.

## Sub-phase 9.4 — Packaging
- [ ] `demos/` — each with a README whose first line names its pillar and its
      memory tier, a one-command run (`make demo-continuity`, `make
      demo-contagion`, `make demo-deposition`), and a reset script so every
      demo is infinitely re-runnable from clean state.
- [ ] All three tenants visible in the console switcher — multi-tenancy and
      isolation are demonstrated by using the product, not by asserting it.
- [ ] A single `make demo-all` that runs all three back to back in under 12
      minutes for a judge with time to spare.

## Definition of Done
- [ ] Three demos, one command each, repeatable from clean state, each
      mapping to a named pillar and a named memory tier.
- [ ] Each demo has a 45-second cut suitable for the video, already edited.
- [ ] `docs/prior-art.md` written: what exists in agent memory today
      (Mem0, Zep, Letta, LangMem, vector DB + Postgres stacks), what they do
      well, and precisely which of our claims are genuinely unmatched. Being
      accurate about this is more persuasive than claiming everything.
**Est: 6 days (partly parallel with Phase 08).**
