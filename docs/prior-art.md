# Prior art

What agent memory looks like today, what each does well, and — specifically
— which of Mnemos's claims are genuinely unmatched rather than just phrased
differently. Written to be checkable, not flattering: if a competitor
already does something claimed below as novel, that is a bug in this
document and should be reported as one.

## Mem0

A memory layer that extracts and updates facts from conversation, with a
managed hosted offering and a self-hosted OSS core. Its update model —
resolving a new extraction against existing memory (add / update /
merge / no-op) — is a genuinely good answer to "memory shouldn't just grow
forever." What it doesn't do: nothing in its public model ties a fact back
to a specific database transaction, and there's no first-class notion of
*jurisdiction* — where a piece of memory is allowed to physically live.
Deleting a memory removes it; there is no distinction between "removed from
the index" and "cryptographically unrecoverable," because nothing forces
that distinction to be load-bearing.

## Zep

Builds a temporal knowledge graph over conversation history, and takes
"when was this true" seriously — closer in spirit to Mnemos's `AS OF SYSTEM
TIME` recall than anything else on this list. The difference is what the
temporal query is *for*: Zep's temporal graph answers "what did the user say
and when," which is a recall-quality feature. Mnemos's is built to answer
"what could this agent have known when it acted," anchored to a
transactionally-consistent audit trail an auditor could subpoena — a
governance feature wearing similar-looking machinery.

## Letta (formerly MemGPT)

The OS-paging metaphor — memory tiers, context windows as RAM, external
storage as disk — is a real and useful mental model, and it's the closest
thing to Mnemos's own episodic → semantic → procedural tiering in spirit.
What it's optimized for is giving one agent a bigger effective context, not
answering for what a *fleet* of agents collectively believed, and it has no
concept of a poisoned memory's blast radius — an agent's own memory is
trusted by construction, because the threat model is capacity, not
adversarial input.

## LangMem (LangChain)

A memory toolkit, not an opinionated store — background/hot-path extraction
utilities and a set of memory-management primitives meant to sit in front of
whatever vector store or database you already run. That flexibility is the
point, and it means LangMem inherits whatever guarantees (or lack of them)
its backing store provides. If you put LangMem in front of a plain vector
database, you get plain-vector-database guarantees: no cross-store
transaction boundary, no residency primitive, no audit ledger — because
LangMem was never trying to be the store.

## The vector-DB + Postgres pattern (the unbranded default)

The most common architecture in production today isn't a product, it's a
pattern: embeddings in a vector database (Pinecone, Weaviate, pgvector),
structured metadata and an app-level audit log in Postgres alongside it. It
works, and it's what most teams reach for by default. Its failure mode is
exactly what motivates this project: an erasure has to be executed twice,
against two systems, with no transaction spanning both — so "delete this
memory" is a best-effort operation across a consistency gap, not an atomic
one. A crash, a partial failure, or a race between the two deletes leaves
the vector store and the audit trail disagreeing about whether something
was actually erased, and nothing in the architecture can even detect that
they disagree.

## What's actually unmatched, specifically

Not "we're better" — three concrete claims, checkable against the public
material of everything above as of this writing:

1. **Jurisdiction as a memory primitive.** `REGIONAL BY ROW` homing, with
   cross-border access limited to policy-approved *derived* projections and
   every crossing logged — not a deployment-region setting, a per-row
   property enforced by the database. None of the above have an equivalent.
2. **A revocation with a computed blast radius.** `revoke_source()` doesn't
   delete a memory; it computes and revokes everything transitively
   descended from a compromised source — facts, corroborations, skills,
   past recalls, declared actions — in one transaction. Nothing above
   publishes an equivalent operation, or a published attack suite measuring
   what it actually catches ([docs/redteam.md](redteam.md)).
3. **A deposition, not a log line.** `explain(action_id)` reconstructs the
   exact facts as they stood at decision time, with hash-verified
   provenance to their source episodes, exportable as HTML that verifies
   itself offline. This requires the audit trail, the facts, and the
   provenance graph to share one transactional boundary — the reason this
   project is a CockroachDB project and not a vector-store-plus-something
   project in the first place ([docs/decisions.md](decisions.md), ADR-003).
