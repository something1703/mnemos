"""Pillar III — contamination closure.

`blast_radius` answers the question no agent memory system can answer today:
*a poisoned fact entered six weeks ago — what did it touch?*

Contamination is not one hop. It flows along a causal graph:

    episode ──(fact_provenance)──▶ fact
    fact    ──(recall_log)──────▶ recall
    recall  ──(action_recalls)──▶ action
    action  ──(same session, later)──▶ episode        ← the second-order edge
    fact    ──(skill_provenance)──▶ skill version

The fourth edge is what makes this hard and worth doing. An agent recalls a
poisoned fact, acts on it, and writes *new* episodes describing what it did.
Those episodes are consolidated into new facts, which look perfectly legitimate:
correct provenance, independent-looking sources, high confidence. Only the
causal path reveals that their ancestry runs through a lie.

The closure is computed **before** anything is destroyed, so `revoke_source` can
show an operator exactly what a revocation will cost before they confirm it.
Over-revocation is as much a bug as under-revocation, and both directions are
tested.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("mnemos.integrity")

MAX_DEPTH = 8
"""Hard bound on causal depth.

Not a correctness limit — the graph is acyclic in practice — but a guard against
a cycle introduced by a future edge type turning a revocation preview into an
unbounded query. If a real radius ever hits it, the result says so rather than
silently truncating."""


class Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ContaminatedFact(Base):
    fact_id: UUID
    subject_key: str
    fact_kind: str
    trust: str
    depth: int
    """0 = derived directly from the source. Higher = laundered through
    intervening agent activity, which is where the damage hides."""


class ContaminatedSkill(Base):
    skill_id: UUID
    version: int
    name: str
    trust: str
    depth: int


class BlastRadius(Base):
    """What a source touched, and how far the damage travelled."""

    tenant_id: UUID
    source_event_ids: list[UUID]

    facts: list[ContaminatedFact] = Field(default_factory=list)
    skills: list[ContaminatedSkill] = Field(default_factory=list)
    recall_ids: list[UUID] = Field(default_factory=list)
    action_ids: list[UUID] = Field(default_factory=list)
    derived_event_ids: list[UUID] = Field(default_factory=list)
    """Episodes an agent wrote after acting on contaminated memory. These are
    the laundering step: their derived facts look clean on inspection."""

    max_depth_reached: int = 0
    truncated: bool = False
    computed_at: datetime | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.facts or self.skills or self.recall_ids or self.action_ids)

    def manifest(self) -> dict[str, object]:
        """The record stored on the revocation row.

        Retained forever: "what did we revoke, and what did it touch" is asked
        months later by someone who was not there.
        """
        return {
            "source_event_ids": [str(e) for e in self.source_event_ids],
            "counts": {
                "facts": len(self.facts),
                "skills": len(self.skills),
                "recalls": len(self.recall_ids),
                "actions": len(self.action_ids),
                "derived_episodes": len(self.derived_event_ids),
            },
            "fact_ids": [str(f.fact_id) for f in self.facts],
            "skill_versions": [[str(s.skill_id), s.version] for s in self.skills],
            "recall_ids": [str(r) for r in self.recall_ids],
            "action_ids": [str(a) for a in self.action_ids],
            "derived_event_ids": [str(e) for e in self.derived_event_ids],
            "max_depth": self.max_depth_reached,
            "truncated": self.truncated,
        }

    def summary(self) -> str:
        return (
            f"{len(self.source_event_ids)} source(s) → {len(self.facts)} fact(s) → "
            f"{len(self.skills)} skill(s) → {len(self.recall_ids)} recall(s) → "
            f"{len(self.action_ids)} action(s), depth {self.max_depth_reached}"
        )


async def blast_radius(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    source_event_ids: list[UUID],
    *,
    max_depth: int = MAX_DEPTH,
    include_second_order: bool = True,
) -> BlastRadius:
    """Compute the transitive contamination closure from one or more episodes.

    Iterative rather than a single recursive CTE. The frontier alternates between
    node types (episode → fact → recall → action → episode), which a `WITH
    RECURSIVE` can express only by unioning heterogeneous shapes into one wide
    row — correct but unreadable, and unreadable is a poor property for the query
    an incident responder reads at 3am while deciding what to destroy.
    """
    facts: dict[UUID, ContaminatedFact] = {}
    skills: dict[tuple[UUID, int], ContaminatedSkill] = {}
    recall_ids: set[UUID] = set()
    action_ids: set[UUID] = set()
    derived_events: set[UUID] = set()

    frontier_events = set(source_event_ids)
    seen_events = set(source_event_ids)
    depth = 0
    truncated = False

    while frontier_events:
        if depth >= max_depth:
            truncated = True
            log.warning(
                "blast radius hit the depth bound",
                extra={"tenant_id": str(tenant_id), "depth": depth},
            )
            break

        # episode → fact
        await cur.execute(
            """
            SELECT DISTINCT f.fact_id, f.subject_key, f.fact_kind, f.trust
            FROM mnemos.fact_provenance p
            JOIN mnemos.semantic_facts f
              ON f.tenant_id = p.tenant_id AND f.fact_id = p.fact_id
            WHERE p.tenant_id = %s AND p.event_id = ANY(%s)
            """,
            (tenant_id, list(frontier_events)),
        )
        new_facts = [
            ContaminatedFact(
                fact_id=r[0], subject_key=r[1], fact_kind=r[2], trust=str(r[3]), depth=depth
            )
            for r in await cur.fetchall()
            if r[0] not in facts
        ]
        for fact in new_facts:
            facts[fact.fact_id] = fact

        if not new_facts:
            break

        new_fact_ids = [f.fact_id for f in new_facts]

        # fact → skill version
        await cur.execute(
            """
            SELECT DISTINCT sp.skill_id, sp.version, s.name, sv.trust
            FROM mnemos.skill_provenance sp
            JOIN mnemos.skills s ON s.tenant_id = sp.tenant_id AND s.skill_id = sp.skill_id
            JOIN mnemos.skill_versions sv
              ON sv.tenant_id = sp.tenant_id AND sv.skill_id = sp.skill_id
                 AND sv.version = sp.version
            WHERE sp.tenant_id = %s AND sp.fact_id = ANY(%s)
            """,
            (tenant_id, new_fact_ids),
        )
        for r in await cur.fetchall():
            key = (r[0], int(r[1]))
            if key not in skills:
                skills[key] = ContaminatedSkill(
                    skill_id=r[0], version=int(r[1]), name=r[2], trust=str(r[3]), depth=depth
                )

        # fact → recall
        await cur.execute(
            "SELECT DISTINCT recall_id FROM mnemos.recall_log "
            "WHERE tenant_id = %s AND fact_id = ANY(%s)",
            (tenant_id, new_fact_ids),
        )
        new_recalls = {r[0] for r in await cur.fetchall()} - recall_ids
        recall_ids |= new_recalls

        if not new_recalls or not include_second_order:
            depth += 1
            frontier_events = set()
            continue

        # recall → action
        await cur.execute(
            "SELECT DISTINCT action_id FROM mnemos.action_recalls "
            "WHERE tenant_id = %s AND recall_id = ANY(%s)",
            (tenant_id, list(new_recalls)),
        )
        new_actions = {r[0] for r in await cur.fetchall()} - action_ids
        action_ids |= new_actions

        if not new_actions:
            depth += 1
            frontier_events = set()
            continue

        # action → episodes the agent wrote afterwards in the same session
        #
        # The laundering step. Restricted to the same session AND to episodes
        # recorded after the action, because "later, elsewhere, by anyone" is not
        # causation — and a closure that over-reaches would revoke innocent
        # memory, which is its own kind of harm.
        await cur.execute(
            """
            SELECT DISTINCT e.event_id
            FROM mnemos.action_log a
            JOIN mnemos.episodic_events e
              ON e.tenant_id = a.tenant_id
                 AND e.session_id = a.session_id
                 AND e.occurred_at >= a.declared_at
            WHERE a.tenant_id = %s AND a.action_id = ANY(%s)
                  AND e.source_trust IN ('agent', 'external')
            """,
            (tenant_id, list(new_actions)),
        )
        next_events = {r[0] for r in await cur.fetchall()} - seen_events
        derived_events |= next_events
        seen_events |= next_events
        frontier_events = next_events
        depth += 1

    return BlastRadius(
        tenant_id=tenant_id,
        source_event_ids=list(source_event_ids),
        facts=sorted(facts.values(), key=lambda f: (f.depth, str(f.fact_id))),
        skills=sorted(skills.values(), key=lambda s: (s.depth, str(s.skill_id))),
        recall_ids=sorted(recall_ids, key=str),
        action_ids=sorted(action_ids, key=str),
        derived_event_ids=sorted(derived_events, key=str),
        max_depth_reached=max(depth - 1, 0) if facts else 0,
        truncated=truncated,
    )


async def blast_radius_for_subject(
    cur: psycopg.AsyncCursor, tenant_id: UUID, subject_key: str, **kwargs: object
) -> BlastRadius:
    """Convenience wrapper: every episode for a subject as the source set."""
    await cur.execute(
        "SELECT event_id FROM mnemos.episodic_events WHERE tenant_id = %s AND subject_key = %s",
        (tenant_id, subject_key),
    )
    event_ids = [r[0] for r in await cur.fetchall()]
    if not event_ids:
        return BlastRadius(tenant_id=tenant_id, source_event_ids=[])
    return await blast_radius(cur, tenant_id, event_ids, **kwargs)  # type: ignore[arg-type]
