# Phase 08 — Console UI

**Objective:** `apps/console` — a Next.js dashboard that makes invisible
infrastructure *visible*: memory tiers, the trust lattice, physical residency,
the living audit chain, blast radius, depositions, and erasure-with-proof.
This is what the video films and what a judge will actually click.

Brand tokens from Phase 01 are law. The four accent colors carry meaning:
teal = recall, amber = consolidation, red = destruction, violet = doubt. A
judge should be able to read system state from color alone.

## Inputs needed from the user
1. Where to host (default: AWS Amplify Hosting or S3 + CloudFront — keeps the
   all-AWS story; user confirms).
2. Approval of each screen at wireframe stage before high-fidelity build.

## Sub-phase 8.1 — App skeleton & design system
- [ ] Next.js (app router) + Tailwind wired to `brand/tailwind.tokens.js`;
      fonts loaded (Bricolage Grotesque / Albert Sans / Spline Sans Mono).
- [ ] Core components: Shell (nav rail + memory-trace footer), Card, Stat,
      TrustBadge (five states, umbra for doubt), SeverityBadge, RegionChip
      (flag-free, code + name, mono), HashLink (mono, middle-truncated,
      copy-on-click), EmptyState (every empty screen instructs the next
      action — no moods, no shrugging illustrations).
- [ ] The **memory trace** signature: footer strip rendering the tenant's
      latest ledger commits as linked dots fed by the SSE stream — tinted by
      op class, dashed link at every erasure, heavier anchor node at every
      Merkle checkpoint, stacked lanes per shard converging at checkpoints.
      Respect `prefers-reduced-motion`.
**Accept:** a `/kitchen-sink` route shows every component in every state; the
memory trace renders live against real ledger data; user signs off.

## Sub-phase 8.2 — Screens (wireframe → approve → build, in this order)

1. **Overview** — tier counts, trust distribution (a stacked bar that makes
   `unverified` volume impossible to ignore), consolidation lag, chain height
   per shard, last checkpoint + its S3 anchor link, last sweep. Hero stat:
   *facts trusted* — deliberately not *facts stored*, because the number that
   matters is the number you'd act on.

2. **Memory Explorer** — search box = live hybrid recall. Results show the
   score breakdown (similarity × strength × confidence × trust) on hover, the
   trust badge, and the home region. Fact detail drawer: provenance episodes,
   supersession chain, contest counterparts side-by-side with their evidence,
   recall-history sparkline, and "who has recalled this" (the accountability
   view nobody ships).

3. **Time Machine** — a scrubber over the temporal window. Drag it and the
   Explorer re-renders `recall_as_of(t)`: facts appear, change trust state,
   get superseded, get revoked. Grey-out beyond the GC boundary with the real
   `gc.ttlseconds` value stated, not hidden. *Watching an agent's mind change
   over time is the most legible thing in this product — make this screen
   excellent.*

4. **Residency** — a map (or clean region-lane diagram; no decorative globe)
   showing where each subject class physically lives, the projection policy
   per boundary, and a live log of region crossings with the policy that
   permitted each. Attempted denials shown in umbra.

5. **Ledger** — the chain visualized per shard, converging at checkpoints.
   VALID badge from a live verifier run, plus a second badge for the
   **anchored** verdict from S3. Clicking a row shows its exact hash inputs.
   Tamper-demo mode (staging only) lets a judge break a row and watch both
   badges react differently — internal chain vs external anchor.

6. **Custodian** — findings by run, severity-filtered; each finding links the
   skill that produced it and whether it came from the Cloud MCP Server or
   `ccloud`. Governance proposals appear here with approve/reject, and
   approval demands the admin key. Skill names are first-class UI elements —
   judges should see the sponsor's own skills working.

7. **Blast Radius** — enter a source episode; get the transitive contamination
   graph (facts → corroborations → skills → recalls → actions) with counts per
   hop, before anything is touched. Then `Revoke source` — signal red,
   type-to-confirm, dual-control gate — and watch the graph turn violet in
   real time via the revocation changefeed. **Video Moment #4.**

8. **Deposition** — enter or click an action; get the full causal chain
   rendered as a document: what the agent did, which recalls caused it, what
   those facts said *at that moment*, their provenance to raw episodes,
   contamination flags, and the verification result against the S3 anchor.
   One button: **Export** → self-contained, offline-verifying HTML.

9. **Forget** — the flagship flow: enter subject_key → choose mode (redact /
   forget / quarantine / shred, each with plain-language consequences) →
   exact preview of what dies → **legal-hold check surfaced before the button,
   not after** → type-to-confirm in signal red (the only place signal red is
   a button) → executing animation along the memory trace → proof screen: the
   new ledger row, chain re-verified VALID, anchor re-verified, and recall for
   the subject returning empty. Screen-recordable in one take.

**Accept per screen:** wireframe approved → built → reviewed against brand and
copy rules (active voice, verbs on buttons: "Forget subject", "Revoke source";
toast: "Forgotten — proof committed at #<shard>/<seq>").

## Sub-phase 8.3 — Data wiring & auth
- [ ] Typed client generated from the Phase 04 OpenAPI schema.
- [ ] Read-scoped key for browsing; admin key re-entry gated inside the Forget
      and Revoke flows only, never held in local storage.
- [ ] SSE for the ledger, fact stream, and revocation bus; polling fallback
      (ledger 5s, overview 15s) when SSE is unavailable.
- [ ] Tenant switcher — the three demo verticals are three tenants, so
      isolation is visible by switching, and a judge can confirm that
      switching tenants changes everything including the chain.

## Sub-phase 8.4 — Deploy & polish
- [ ] Deploy to the chosen host; custom loading / empty / error states
      everywhere; visible keyboard focus; responsive to tablet width.
- [ ] Lighthouse: no console errors, a11y ≥ 95. Color is never the sole
      carrier of meaning — every trust state has a label and a shape, so the
      four-accent system survives color blindness.
- [ ] A read-only **judge tenant** with a pre-seeded, story-complete dataset
      and no key required, linked from the README. A judge who does not want
      to install anything must still be able to see everything.

## Definition of Done
- [ ] All nine screens live against real deployed services with real data.
- [ ] Forget and Revoke flows filmed as rehearsal clips.
- [ ] Judge tenant reachable and self-explanatory to someone with no context.
**Est: 8 days. Runs partly in parallel with Phase 09.**
