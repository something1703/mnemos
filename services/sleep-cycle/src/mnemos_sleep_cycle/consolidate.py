"""Consolidation: turn a session's episodes into candidate facts, run each one
through belief revision, and commit — one serializable transaction per
`(tenant, session, subject)` batch.

**Batches are grouped by subject, not just by session.** That single choice is
what keeps consolidation residency-safe without needing to trust a model to
reason about jurisdiction: a fact's `subject_key` is inherited directly from
the episodes it was distilled from, never invented by the model, and since
`home_region` is a pure function of `subject_key` (`residency.resolve`),
grouping by subject makes "a batch never spans two regions" true by
construction rather than by hoping the grouping happens to hold.

**Provenance edges are written before the fact row that cites them**, even
though nothing enforces an FK between the two tables. The reason is migration
010's `require_provenance` trigger: it fires on every INSERT or UPDATE to
`semantic_facts` and, for any row landing at `corroborated` or `trusted`,
checks that at least one provenance edge already exists for that `fact_id`. A
fact whose dominant source is `system` or `operator` is trusted on arrival
(Phase 05.4), which means its very first INSERT must already satisfy that
check — so the edges have to exist first.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

import psycopg
from mnemos_engine.crypto import DecryptionFailed, Envelope, row_aad
from mnemos_engine.embeddings import Embedder, to_pgvector
from mnemos_engine.ledger import append_audit
from mnemos_engine.llm import ChatClient, LLMError
from mnemos_engine.models import Op, SourceTrust, Trust

from . import revise
from .corroboration import apply_trust_transition
from .distill import DistilledFact, EpisodeInput, distill_session

log = logging.getLogger("mnemos.sleep_cycle.consolidate")

DEFAULT_BATCH_LIMIT = 25
"""MAX_SESSIONS_PER_RUN. Caps how many (tenant, session, subject) groups one
invocation processes, so a run has a bounded, predictable cost regardless of
backlog size — the next run picks up where this one left off."""

REINFORCE_STRENGTH_DELTA = 0.15

_TRUST_RANK: dict[SourceTrust, int] = {
    SourceTrust.SYSTEM: 3,
    SourceTrust.OPERATOR: 2,
    SourceTrust.AGENT: 1,
    SourceTrust.EXTERNAL: 0,
}


@dataclass(frozen=True)
class SessionBatch:
    tenant_id: UUID
    session_id: UUID
    subject_key: str
    home_region: str
    episode_count: int


@dataclass
class ConsolidationOutcome:
    batch: SessionBatch
    episodes_marked: int = 0
    facts_novel: int = 0
    facts_reinforced: int = 0
    facts_superseded: int = 0
    facts_contested: int = 0
    facts_dropped_no_source: int = 0
    facts_dropped_invalid: int = 0
    llm_error: str | None = None

    @property
    def facts_written(self) -> int:
        return (
            self.facts_novel + self.facts_reinforced + self.facts_superseded + self.facts_contested
        )


def _dominant_source_trust(sources: list[SourceTrust]) -> SourceTrust:
    """The most-trusted origin among a fact's cited episodes.

    A single system- or operator-sourced episode is enough to promote a fact
    directly (Phase 05.4's "trusted on arrival" rule), so when a distilled fact
    cites episodes of mixed origin, the highest-trust one governs — the fact is
    only as untrustworthy as its LEAST trusted citation would make it if we
    went the other way, and the spec is explicit that system/operator
    provenance is dispositive, not averaged.
    """
    return max(sources, key=lambda s: _TRUST_RANK[s])


async def find_unconsolidated_batches(
    cur: psycopg.AsyncCursor, *, limit: int = DEFAULT_BATCH_LIMIT
) -> list[SessionBatch]:
    """The oldest not-yet-consolidated `(tenant, session, subject, region)`
    groups, capped per run. Call inside a `tenant_id=None, read_only=True`
    transaction — this is the one query in the sleep cycle that must see
    across every tenant, which is exactly what `mnemos_pipeline`'s BYPASSRLS
    grant (migration 011) exists for.
    """
    await cur.execute(
        """
        SELECT tenant_id, session_id, subject_key, home_region, count(*)
        FROM mnemos.episodic_events
        WHERE consolidated_at IS NULL
        GROUP BY tenant_id, session_id, subject_key, home_region
        ORDER BY min(occurred_at)
        LIMIT %s
        """,
        (limit,),
    )
    return [
        SessionBatch(
            tenant_id=row[0],
            session_id=row[1],
            subject_key=row[2],
            home_region=row[3],
            episode_count=int(row[4]),
        )
        for row in await cur.fetchall()
    ]


async def _fetch_episodes(
    cur: psycopg.AsyncCursor, batch: SessionBatch, *, envelope: Envelope
) -> list[EpisodeInput]:
    await cur.execute(
        "SELECT event_id, occurred_at, content_ciphertext, content_dek_wrapped, source_trust "
        "FROM mnemos.episodic_events "
        "WHERE tenant_id = %s AND subject_key = %s AND session_id = %s "
        "AND consolidated_at IS NULL ORDER BY occurred_at",
        (batch.tenant_id, batch.subject_key, batch.session_id),
    )
    episodes: list[EpisodeInput] = []
    for i, row in enumerate(await cur.fetchall(), start=1):
        event_id, occurred_at, ciphertext, wrapped, source_trust = row
        try:
            content = envelope.decrypt(
                bytes(ciphertext), bytes(wrapped), aad=row_aad(batch.tenant_id, batch.subject_key)
            )
        except DecryptionFailed:
            # Shredded since it was written — its provenance still matters
            # historically, but there is nothing left to distill from it.
            log.info("skipping undecryptable episode %s (shredded)", event_id)
            continue
        episodes.append(
            EpisodeInput(
                index=i,
                event_id=event_id,
                occurred_at=occurred_at,
                content=content,
                source_trust=SourceTrust(source_trust),
            )
        )
    return episodes


async def _insert_fact(
    cur: psycopg.AsyncCursor,
    batch: SessionBatch,
    distilled: DistilledFact,
    *,
    vector: list[float],
    source_episodes: list[EpisodeInput],
    envelope: Envelope,
    actor: str,
    outcome_label: str,
) -> UUID:
    """Write one new fact row and its provenance edges, in that order.

    See the module docstring for why provenance comes first. `outcome_label`
    is carried into the audit payload only — it never affects behaviour — so a
    reviewer reading the ledger can tell "novel" apart from "the new side of a
    supersede/contest" without cross-referencing a second table.
    """
    fact_id = uuid4()
    sources = [ep.source_trust for ep in source_episodes]
    dominant = _dominant_source_trust(sources)
    initial_trust = Trust.TRUSTED if dominant.is_trusted_on_arrival else Trust.UNVERIFIED

    await append_audit(
        cur,
        batch.tenant_id,
        op=Op.CONSOLIDATE,
        actor=actor,
        subject_key=batch.subject_key,
        payload={
            "fact_id": str(fact_id),
            "outcome": outcome_label,
            "fact_kind": distilled.fact_kind,
            "confidence": str(distilled.confidence),
            "dominant_source_trust": str(dominant),
            "source_event_ids": [str(ep.event_id) for ep in source_episodes],
        },
    )
    for episode in source_episodes:
        await cur.execute(
            "INSERT INTO mnemos.fact_provenance (tenant_id, fact_id, event_id, subject_key) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (batch.tenant_id, fact_id, episode.event_id, batch.subject_key),
        )

    ciphertext, wrapped = envelope.encrypt(
        distilled.fact_text, aad=row_aad(batch.tenant_id, batch.subject_key)
    )
    text_hash = hashlib.sha256(distilled.fact_text.encode("utf-8")).digest()

    await cur.execute(
        """
        INSERT INTO mnemos.semantic_facts
            (tenant_id, fact_id, home_region, subject_key, fact_kind, text_ciphertext,
             text_dek_wrapped, text_hash, embedding, tsv, trust, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::VECTOR, to_tsvector('english', %s), %s, %s)
        """,
        (
            batch.tenant_id,
            fact_id,
            batch.home_region,
            batch.subject_key,
            distilled.fact_kind,
            ciphertext,
            wrapped,
            text_hash,
            to_pgvector(vector),
            distilled.fact_text,
            str(initial_trust),
            distilled.confidence,
        ),
    )

    # Corroboration_count starts at the DB default (0) regardless of
    # `initial_trust`; recomputing now populates it from this fact's own
    # provenance so later classify() calls comparing against it see the truth,
    # not a zero that undercounts an already-trusted fact.
    await apply_trust_transition(cur, batch.tenant_id, fact_id, batch.subject_key, actor=actor)
    return fact_id


async def _reinforce_existing(
    cur: psycopg.AsyncCursor,
    batch: SessionBatch,
    match_fact_id: UUID,
    *,
    source_episodes: list[EpisodeInput],
    actor: str,
) -> None:
    await append_audit(
        cur,
        batch.tenant_id,
        op=Op.REINFORCE,
        actor=actor,
        subject_key=batch.subject_key,
        payload={
            "fact_id": str(match_fact_id),
            "source_event_ids": [str(ep.event_id) for ep in source_episodes],
        },
    )
    for episode in source_episodes:
        await cur.execute(
            "INSERT INTO mnemos.fact_provenance (tenant_id, fact_id, event_id, subject_key) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (batch.tenant_id, match_fact_id, episode.event_id, batch.subject_key),
        )
    await cur.execute(
        "UPDATE mnemos.semantic_facts SET strength = strength + %s, updated_at = now() "
        "WHERE tenant_id = %s AND fact_id = %s",
        (REINFORCE_STRENGTH_DELTA, batch.tenant_id, match_fact_id),
    )
    await apply_trust_transition(
        cur, batch.tenant_id, match_fact_id, batch.subject_key, actor=actor
    )


async def _link_contested(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    subject_key: str,
    left: UUID,
    right: UUID,
    *,
    actor: str,
) -> None:
    for this, other in ((left, right), (right, left)):
        await append_audit(
            cur,
            tenant_id,
            op=Op.CONTEST,
            actor=actor,
            subject_key=subject_key,
            payload={"fact_id": str(this), "contested_with": str(other)},
        )
        await cur.execute(
            "UPDATE mnemos.semantic_facts "
            "SET trust = 'contested', contested_with = %s, updated_at = now() "
            "WHERE tenant_id = %s AND fact_id = %s",
            (other, tenant_id, this),
        )


async def _supersede(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    subject_key: str,
    *,
    winner: UUID,
    loser: UUID,
    actor: str,
) -> None:
    await append_audit(
        cur,
        tenant_id,
        op=Op.SUPERSEDE,
        actor=actor,
        subject_key=subject_key,
        payload={"superseded": str(loser), "superseded_by": str(winner)},
    )
    await cur.execute(
        "UPDATE mnemos.semantic_facts SET superseded_by = %s, updated_at = now() "
        "WHERE tenant_id = %s AND fact_id = %s",
        (winner, tenant_id, loser),
    )


async def _mark_consolidated(
    cur: psycopg.AsyncCursor,
    batch: SessionBatch,
    event_ids: list[UUID],
    *,
    actor: str,
) -> None:
    await append_audit(
        cur,
        batch.tenant_id,
        op=Op.CONSOLIDATE,
        actor=actor,
        subject_key=batch.subject_key,
        payload={"session_id": str(batch.session_id), "episodes_marked": len(event_ids)},
    )
    if event_ids:
        await cur.execute(
            "UPDATE mnemos.episodic_events SET consolidated_at = now() "
            "WHERE tenant_id = %s AND subject_key = %s AND event_id = ANY(%s)",
            (batch.tenant_id, batch.subject_key, event_ids),
        )


async def consolidate_batch(
    cur: psycopg.AsyncCursor,
    batch: SessionBatch,
    *,
    chat: ChatClient,
    embedder: Embedder,
    envelope: Envelope,
    actor: str = "system:sleep-cycle",
) -> ConsolidationOutcome:
    """Distill, revise, and commit one batch. Call inside one transaction per
    batch (`Database.transaction(batch.tenant_id, ...)`) — that transaction
    boundary is what makes a crash mid-run safe: episodes are only marked
    consolidated in the same commit as the facts derived from them, so a died
    run leaves the batch untouched for the next one to retry, never half-done.
    """
    episodes = await _fetch_episodes(cur, batch, envelope=envelope)
    outcome = ConsolidationOutcome(batch=batch)

    if not episodes:
        # Every episode in this batch was undecryptable (shredded). Nothing to
        # distill, but the rows still need marking or this batch is retried
        # forever.
        await _mark_consolidated(cur, batch, [], actor=actor)
        return outcome

    try:
        distillation = await distill_session(chat, episodes)
    except LLMError as exc:
        # The batch's episodes stay unconsolidated — this transaction is about
        # to be abandoned by the caller (no mark_consolidated call), so the
        # next run retries the same batch rather than silently losing it.
        log.warning("distillation failed for session %s: %s", batch.session_id, exc)
        outcome.llm_error = str(exc)
        raise

    outcome.facts_dropped_no_source = distillation.dropped_no_source
    outcome.facts_dropped_invalid = distillation.dropped_invalid

    episodes_by_index = {ep.index: ep for ep in episodes}
    all_event_ids = [ep.event_id for ep in episodes]

    for distilled in distillation.facts:
        source_episodes = [
            episodes_by_index[i] for i in distilled.source_indices if i in episodes_by_index
        ]
        if not source_episodes:
            outcome.facts_dropped_no_source += 1
            continue

        vector = (await embedder.embed([distilled.fact_text]))[0]
        candidate_source = _dominant_source_trust([ep.source_trust for ep in source_episodes])

        decision = await revise.classify(
            cur,
            chat,
            batch.tenant_id,
            batch.subject_key,
            candidate_text=distilled.fact_text,
            candidate_vector=vector,
            candidate_confidence=distilled.confidence,
            candidate_source_trust=candidate_source,
            envelope=envelope,
        )

        if decision.outcome is revise.Outcome.NOVEL:
            await _insert_fact(
                cur,
                batch,
                distilled,
                vector=vector,
                source_episodes=source_episodes,
                envelope=envelope,
                actor=actor,
                outcome_label="novel",
            )
            outcome.facts_novel += 1

        elif decision.outcome is revise.Outcome.REINFORCE:
            assert decision.match is not None
            await _reinforce_existing(
                cur, batch, decision.match.fact_id, source_episodes=source_episodes, actor=actor
            )
            outcome.facts_reinforced += 1

        elif decision.outcome is revise.Outcome.CONTEST:
            assert decision.match is not None
            new_fact_id = await _insert_fact(
                cur,
                batch,
                distilled,
                vector=vector,
                source_episodes=source_episodes,
                envelope=envelope,
                actor=actor,
                outcome_label="contest",
            )
            await _link_contested(
                cur,
                batch.tenant_id,
                batch.subject_key,
                new_fact_id,
                decision.match.fact_id,
                actor=actor,
            )
            outcome.facts_contested += 1

        elif decision.outcome is revise.Outcome.SUPERSEDE:
            assert decision.match is not None
            new_fact_id = await _insert_fact(
                cur,
                batch,
                distilled,
                vector=vector,
                source_episodes=source_episodes,
                envelope=envelope,
                actor=actor,
                outcome_label="supersede",
            )
            if decision.new_wins:
                await _supersede(
                    cur,
                    batch.tenant_id,
                    batch.subject_key,
                    winner=new_fact_id,
                    loser=decision.match.fact_id,
                    actor=actor,
                )
            else:
                await _supersede(
                    cur,
                    batch.tenant_id,
                    batch.subject_key,
                    winner=decision.match.fact_id,
                    loser=new_fact_id,
                    actor=actor,
                )
            outcome.facts_superseded += 1

    await _mark_consolidated(cur, batch, all_event_ids, actor=actor)
    outcome.episodes_marked = len(all_event_ids)
    return outcome


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "ConsolidationOutcome",
    "SessionBatch",
    "consolidate_batch",
    "find_unconsolidated_batches",
]
