"""consolidate_batch end to end: real encrypted episodes in, a scripted model
standing in for OpenAI, real facts and provenance out — against the local
CockroachDB cluster, exercising the actual triggers (invariant 2's audit
ticket, invariant 3's provenance-before-promotion).
"""

from __future__ import annotations

import uuid

import psycopg
from mnemos_engine.crypto import Envelope
from mnemos_engine.db import Database
from mnemos_engine.embeddings import Embedder
from mnemos_engine.engine import MnemosEngine
from mnemos_engine.models import SourceTrust, Trust
from mnemos_sleep_cycle.consolidate import SessionBatch, consolidate_batch

from .conftest import ScriptedChat, remember_episode


async def _fetch_facts(db: Database, tenant_id: uuid.UUID, subject_key: str) -> list[tuple]:
    async def run(cur: psycopg.AsyncCursor) -> list[tuple]:
        await cur.execute(
            "SELECT fact_id, trust, strength, corroboration_count, superseded_by, contested_with "
            "FROM mnemos.semantic_facts WHERE tenant_id = %s AND subject_key = %s "
            "ORDER BY created_at",
            (tenant_id, subject_key),
        )
        return await cur.fetchall()

    return await db.transaction(tenant_id, run, label="fetch_facts", read_only=True)


async def _episode_consolidated(
    db: Database, tenant_id: uuid.UUID, subject_key: str, event_id: uuid.UUID
) -> bool:
    async def run(cur: psycopg.AsyncCursor) -> bool:
        await cur.execute(
            "SELECT consolidated_at FROM mnemos.episodic_events "
            "WHERE tenant_id = %s AND subject_key = %s AND event_id = %s",
            (tenant_id, subject_key, event_id),
        )
        row = await cur.fetchone()
        return row is not None and row[0] is not None

    return await db.transaction(tenant_id, run, label="check_consolidated", read_only=True)


async def test_consolidate_novel_fact_from_system_episode_is_trusted_on_arrival(
    db: Database, tenant: uuid.UUID, engine: MnemosEngine, embedder: Embedder, envelope: Envelope
) -> None:
    subject = "service:golden-consolidate-1"
    session_id = uuid.uuid4()
    episode = await remember_episode(
        engine,
        tenant,
        subject_key=subject,
        session_id=session_id,
        content="Backup job failed at 02:14 UTC; retry succeeded at 02:31 UTC.",
        source_trust=SourceTrust.SYSTEM,
    )

    chat = ScriptedChat(
        [
            [
                {
                    "fact_text": "Backup job failed at 02:14 UTC; retry succeeded at 02:31 UTC.",
                    "fact_kind": "event",
                    "confidence": 0.9,
                    "source_indices": [1],
                }
            ]
        ]
    )
    batch = SessionBatch(
        tenant_id=tenant,
        session_id=session_id,
        subject_key=subject,
        home_region="us-east-1",
        episode_count=1,
    )

    async def run(cur: psycopg.AsyncCursor):
        return await consolidate_batch(
            cur, batch, chat=chat, embedder=embedder, envelope=envelope, actor="test"
        )

    outcome = await db.transaction(tenant, run, label="consolidate")
    assert outcome.facts_novel == 1
    assert outcome.episodes_marked == 1

    rows = await _fetch_facts(db, tenant, subject)
    assert len(rows) == 1
    assert Trust(rows[0][1]) is Trust.TRUSTED  # system source -> trusted on arrival

    assert await _episode_consolidated(db, tenant, subject, episode.event_id)


async def test_consolidate_agent_episode_lands_unverified(
    db: Database, tenant: uuid.UUID, engine: MnemosEngine, embedder: Embedder, envelope: Envelope
) -> None:
    subject = "patient:us:golden-consolidate-2"
    session_id = uuid.uuid4()
    await remember_episode(
        engine,
        tenant,
        subject_key=subject,
        session_id=session_id,
        content="Patient mentioned a possible penicillin allergy.",
        source_trust=SourceTrust.AGENT,
    )

    chat = ScriptedChat(
        [
            [
                {
                    "fact_text": "Possible penicillin allergy, unconfirmed.",
                    "fact_kind": "attribute",
                    "confidence": 0.6,
                    "source_indices": [1],
                }
            ]
        ]
    )
    batch = SessionBatch(
        tenant_id=tenant,
        session_id=session_id,
        subject_key=subject,
        home_region="us-east-1",
        episode_count=1,
    )

    async def run(cur: psycopg.AsyncCursor):
        return await consolidate_batch(
            cur, batch, chat=chat, embedder=embedder, envelope=envelope, actor="test"
        )

    outcome = await db.transaction(tenant, run, label="consolidate")
    assert outcome.facts_novel == 1

    rows = await _fetch_facts(db, tenant, subject)
    assert Trust(rows[0][1]) is Trust.UNVERIFIED  # agent source -> not trusted on arrival


async def test_consolidate_reinforces_across_independent_sessions_to_corroborated(
    db: Database, tenant: uuid.UUID, engine: MnemosEngine, embedder: Embedder, envelope: Envelope
) -> None:
    """Two DIFFERENT sessions, two DIFFERENT untrusted source_trust categories
    (agent, external — neither is system/operator), both distilling to the
    exact same claim: this is what genuine independent corroboration through
    the whole pipeline looks like, and it should promote to CORROBORATED, not
    TRUSTED (no system/operator provenance was ever involved)."""
    subject = "patient:us:golden-consolidate-3"
    session_a, session_b = uuid.uuid4(), uuid.uuid4()
    claim = "Patient prefers morning appointments."

    await remember_episode(
        engine,
        tenant,
        subject_key=subject,
        session_id=session_a,
        content="I'd prefer morning appointments if possible.",
        source_trust=SourceTrust.AGENT,
    )
    await remember_episode(
        engine,
        tenant,
        subject_key=subject,
        session_id=session_b,
        content="Uploaded intake form indicates a preference for morning slots.",
        source_trust=SourceTrust.EXTERNAL,
    )

    chat = ScriptedChat(
        [
            [
                {
                    "fact_text": claim,
                    "fact_kind": "preference",
                    "confidence": 0.8,
                    "source_indices": [1],
                }
            ],
            [
                {
                    "fact_text": claim,
                    "fact_kind": "preference",
                    "confidence": 0.8,
                    "source_indices": [1],
                }
            ],
            {"contradictory": False, "reason": "identical claim"},
        ]
    )

    batch_a = SessionBatch(
        tenant_id=tenant,
        session_id=session_a,
        subject_key=subject,
        home_region="us-east-1",
        episode_count=1,
    )
    batch_b = SessionBatch(
        tenant_id=tenant,
        session_id=session_b,
        subject_key=subject,
        home_region="us-east-1",
        episode_count=1,
    )

    async def run_a(cur: psycopg.AsyncCursor):
        return await consolidate_batch(
            cur, batch_a, chat=chat, embedder=embedder, envelope=envelope, actor="test"
        )

    async def run_b(cur: psycopg.AsyncCursor):
        return await consolidate_batch(
            cur, batch_b, chat=chat, embedder=embedder, envelope=envelope, actor="test"
        )

    outcome_a = await db.transaction(tenant, run_a, label="consolidate_a")
    assert outcome_a.facts_novel == 1

    outcome_b = await db.transaction(tenant, run_b, label="consolidate_b")
    assert outcome_b.facts_reinforced == 1
    assert outcome_b.facts_novel == 0

    rows = await _fetch_facts(db, tenant, subject)
    assert len(rows) == 1, "reinforcement must not create a second row"
    _fact_id, trust, strength, corroboration_count, superseded_by, contested_with = rows[0]
    assert Trust(trust) is Trust.CORROBORATED
    assert corroboration_count == 2
    assert strength > 1.0
    assert superseded_by is None
    assert contested_with is None


async def test_consolidate_contradiction_produces_contest(
    db: Database, tenant: uuid.UUID, engine: MnemosEngine, embedder: Embedder, envelope: Envelope
) -> None:
    subject = "patient:us:golden-consolidate-4"
    session_a, session_b = uuid.uuid4(), uuid.uuid4()

    await remember_episode(
        engine,
        tenant,
        subject_key=subject,
        session_id=session_a,
        content="Patient prefers the north clinic location.",
        source_trust=SourceTrust.AGENT,
    )
    await remember_episode(
        engine,
        tenant,
        subject_key=subject,
        session_id=session_b,
        content="Patient prefers the north clinic location.",  # same text -> cosine 1.0
        source_trust=SourceTrust.AGENT,
    )

    chat = ScriptedChat(
        [
            [
                {
                    "fact_text": "Patient prefers the north clinic location.",
                    "fact_kind": "preference",
                    "confidence": 0.7,
                    "source_indices": [1],
                }
            ],
            [
                {
                    "fact_text": "Patient prefers the north clinic location.",
                    "fact_kind": "preference",
                    "confidence": 0.7,
                    "source_indices": [1],
                }
            ],
            {"contradictory": True, "reason": "engineered contradiction for the test"},
        ]
    )

    batch_a = SessionBatch(
        tenant_id=tenant,
        session_id=session_a,
        subject_key=subject,
        home_region="us-east-1",
        episode_count=1,
    )
    batch_b = SessionBatch(
        tenant_id=tenant,
        session_id=session_b,
        subject_key=subject,
        home_region="us-east-1",
        episode_count=1,
    )

    async def run_a(cur: psycopg.AsyncCursor):
        return await consolidate_batch(
            cur, batch_a, chat=chat, embedder=embedder, envelope=envelope, actor="test"
        )

    async def run_b(cur: psycopg.AsyncCursor):
        return await consolidate_batch(
            cur, batch_b, chat=chat, embedder=embedder, envelope=envelope, actor="test"
        )

    await db.transaction(tenant, run_a, label="consolidate_a")
    outcome_b = await db.transaction(tenant, run_b, label="consolidate_b")
    assert outcome_b.facts_contested == 1

    rows = await _fetch_facts(db, tenant, subject)
    assert len(rows) == 2, "both sides of a contest are retained, never deleted"
    for _fact_id, trust, _strength, _count, superseded_by, contested_with in rows:
        assert Trust(trust) is Trust.CONTESTED
        assert superseded_by is None
        assert contested_with is not None
    assert rows[0][5] == rows[1][0]
    assert rows[1][5] == rows[0][0]
