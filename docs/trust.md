# The trust lattice — specification

This document exists for one reason: the phase plan for the sleep cycle says
"getting this definition right is the entire defense; write it down." This is
that writing-down. If this document and `services/sleep-cycle/src/
mnemos_sleep_cycle/corroboration.py` disagree, **this document is the
contract** and the code is a bug — the same rule `docs/ledger.md` states for
the audit chain, for the same reason: a security property nobody wrote down
precisely is a security property that erodes one small code change at a time
without anyone noticing.

---

## 1. The five trust states

```
UNVERIFIED → CORROBORATED → TRUSTED
     ↓
CONTESTED          (parked; not on the promotion path)
QUARANTINED        (parked; not on the promotion path)
```

Only `CORROBORATED` and `TRUSTED` are recallable by default (`Trust.is_recallable`
in `packages/engine/src/mnemos_engine/models.py`). `UNVERIFIED` and
`QUARANTINED` facts exist, are counted (`recall`'s `unverified_withheld`
field), and can be inspected explicitly (`include_unverified=True`) — but an
agent asking a plain question never sees them. `CONTESTED` facts ARE returned,
paired with their counterpart, because disagreement is information a caller
can act on; silently picking a winner would not be.

A fact enters at `UNVERIFIED` unless its source is trusted on arrival (§2). It
never re-enters `UNVERIFIED` once corroborated or promoted — trust in this
system only relaxes forward or sideways into `CONTESTED`/`QUARANTINED`, never
backward on its own. The only way a `CORROBORATED` or `TRUSTED` fact stops
being live is `supersede` (a stronger contradicting claim wins) or an explicit
Warden operation (`revoke_source`, `forget`) — never a corroboration-count
recompute.

## 2. Promotion, precisely

`corroboration.determine_trust(current, corroboration_count, has_trusted_source)`:

| current | has_trusted_source | corroboration_count | → |
|---|---|---|---|
| `CONTESTED` | any | any | `CONTESTED` (unchanged) |
| `QUARANTINED` | any | any | `QUARANTINED` (unchanged) |
| anything else | `True` | any | `TRUSTED` |
| anything else | `False` | `≥ 2` | `CORROBORATED` |
| anything else | `False` | `< 2` | `UNVERIFIED` |

`has_trusted_source` is `True` when **any** episode a fact's provenance points
at has `source_trust IN ('system', 'operator')` — a deterministic internal
process or an authenticated human. One such episode is dispositive; it is not
averaged against untrusted ones, and it does not need a second corroborating
source. This is `SourceTrust.is_trusted_on_arrival`, reused from the same rule
`packages/engine/src/mnemos_engine/procedural.py` already applies to skills.

`CONTESTED` and `QUARANTINED` are **not** on this table's promotion path on
purpose. A fact that has been contested or quarantined needs an explicit
resolution — a human, a supersession, a fresh TTL window — not a corroboration
count alone. Without this rule, a quarantined fact could accumulate two more
signatures from the same compromised pipeline and walk itself back to
legitimacy purely on volume, which defeats the reason it was quarantined.

## 3. Independence, precisely

This is the sentence the whole defense rests on:

> Two pieces of evidence corroborate a fact only if they come from a
> **different session** AND a **different `source_trust` origin**.

Both conditions, not either. Getting the boolean wrong here is not a subtle
bug — it is the difference between a corroboration gate and decoration:

- **Only "different session" required** → a single actor who can write
  multiple episodes in different sessions (any agent, over enough turns) can
  manufacture independence by itself.
- **Only "different source_trust" required** → doesn't even make sense as a
  gate; two episodes from the same session almost always share one
  `source_trust` value, so this would rarely block anything, and a single
  session with a deliberately mixed-trust episode list could satisfy it
  trivially.
- **Both required** → an attacker needs to compromise two structurally
  different channels — not just write twice, but write twice from sources the
  system treats as meaningfully different kinds of evidence.

### 3.1 What counts as a "signature"

For a fact `F`, walk its provenance graph — `fact_provenance` rows joined to
`episodic_events` — and collect the set of `(session_id, source_trust)` pairs
among the episodes that support it:

```sql
SELECT DISTINCT e.session_id, e.source_trust
FROM mnemos.fact_provenance p
JOIN mnemos.episodic_events e
  ON e.tenant_id = p.tenant_id AND e.event_id = p.event_id
WHERE p.tenant_id = %s AND p.fact_id = %s
```

Call this set `S`. `corroboration_count` is **not** `|S|`.

### 3.2 Why counting distinct pairs is the wrong number

Consider a fact whose provenance spans one session that happened to log two
episodes with different `source_trust` values (e.g. an `agent` turn and an
`operator` correction, both in the same conversation). `S` has two distinct
pairs: `{(session₁, agent), (session₁, operator)}`. Counting `|S|` says 2 —
wrong. Both signatures share `session₁`, so they are not independent of each
other under the rule in §3; a single session's episode mix must never count as
two corroborating sources.

### 3.3 The actual computation: maximum bipartite matching

`corroboration_count` is the size of the **largest subset of `S` that is
pairwise independent** — every pair in the subset differs in both
`session_id` and `source_trust`. This is exactly maximum bipartite matching:
sessions on one side, the four `source_trust` categories
(`system`, `operator`, `agent`, `external`) on the other, an edge wherever a
session contributed that category at least once. `_max_independent_
corroborations` in `corroboration.py` solves it with a plain augmenting-path
search — correct and cheap, since there are at most four categories on one
side.

Worked examples (all proven directly by
`tests/sleep_cycle/test_corroboration.py::test_max_independent_corroborations_*`):

| Provenance signatures | `corroboration_count` | Why |
|---|---|---|
| `{}` | 0 | nothing to corroborate with |
| `{(s₁, agent)}` | 1 | one source, period |
| `{(s₁, agent), (s₁, operator)}` | 1 | same session — not independent, however many trust values it touched |
| `{(s₁, agent), (s₂, agent)}` | 1 | two sessions, but both can only match the single `agent` slot |
| `{(s₁, agent), (s₂, operator)}` | 2 | different session AND different category — genuinely independent |
| five facts, all citing one episode in one session | 1 (**not 5**) | one session, one category, regardless of how many fact ROWS point at it |

The last row is the self-corroboration attack, stated as an identity rather
than a hope: however many facts a compromised distillation step manufactures
from a single episode, they all point back at the same `(session, source_trust)`
signature, and the matching can assign that signature to at most one category
once. `corroboration_count` cannot exceed 1 without a genuinely second,
independent write — proven against the real pipeline (real encryption, real
triggers, real `consolidate_batch`) in
`tests/sleep_cycle/test_self_corroboration.py::
test_five_facts_from_one_malicious_episode_never_self_promote`, and its
companion `test_two_independent_sessions_do_corroborate_the_same_claim` checks
the definition isn't so strict it blocks legitimate corroboration either.

## 4. `corroboration_count` is recomputed, never incremented

Every time `apply_trust_transition` runs (after any operation that adds a
provenance edge — a new fact's first edges, or a reinforcement's additional
ones), it recomputes §3.3 from the live provenance graph and writes the
result, rather than adding to a running total. An incremented counter can
drift from what the graph actually shows if a code path ever adds an edge
without going through the count logic; a recomputed one cannot, by
construction. The extra query on every write is worth that guarantee.

## 5. Two-layer defense, and which layer actually holds

`distill.py`'s prompt asks the model not to comply with instructions embedded
in episode content, and `tests/sleep_cycle/test_golden_eval.py` measures that
this mostly works (100% resistance on the golden set's three adversarial
cases, at time of writing). **Assume it fails anyway.** The corroboration gate
in this document does not depend on the prompt holding — `test_self_
corroboration.py` scripts a chat client that returns exactly what a fully
compromised distillation step would produce, and the gate still holds,
because it is arithmetic over the provenance graph, not a judgment about
whether any particular piece of text looked suspicious. Phase 10 attacks both
layers on purpose; this document is only a contract for the second one.

## 6. TTL quarantine is a separate mechanism

An `UNVERIFIED` fact that never receives a second independent signature
within `MNEMOS_CORROBORATION_TTL_DAYS` (default 30 —
`corroboration.DEFAULT_TTL_DAYS`) moves to `QUARANTINED` via
`quarantine_stale_unverified`, run weekly. This is not part of the promotion
rule in §2 — it is what stops permanently-unverified facts from accumulating
as ambient noise reachable via `include_unverified=True`. Quarantine never
deletes a row; only the Warden holds `DELETE` (invariant 1), and this sweep's
database role (`mnemos_pipeline`) has no such grant regardless.
