"""Belief revision: the four-outcome classifier in revise.py.

FakeEmbedder is deliberately non-semantic (see its docstring), so most of
these tests engineer an exact cosine similarity with
`controlled_similarity_vector` rather than relying on two different strings
happening to land close together. The two cases that DON'T need engineered
vectors — identical text (similarity exactly 1.0) and two unrelated strings
(similarity near zero) — use FakeEmbedder directly, because those ARE
guarantees it makes.
"""

from __future__ import annotations

import uuid

from mnemos_engine.crypto import Envelope
from mnemos_engine.db import Database
from mnemos_engine.embeddings import Embedder
from mnemos_engine.models import SourceTrust, Trust
from mnemos_sleep_cycle import revise

from .conftest import ScriptedChat, controlled_similarity_vector, seed_fact


async def test_novel_when_no_existing_facts(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    chat = ScriptedChat([])
    vector = (await embedder.embed(["a brand new claim about this subject"]))[0]

    async def run(cur):
        return await revise.classify(
            cur,
            chat,
            tenant,
            "patient:us:novel-test",
            candidate_text="a brand new claim about this subject",
            candidate_vector=vector,
            candidate_confidence=0.8,
            candidate_source_trust=SourceTrust.AGENT,
            envelope=envelope,
        )

    decision = await db.transaction(tenant, run, label="classify")
    assert decision.outcome is revise.Outcome.NOVEL
    assert decision.match is None
    assert chat.calls == []


async def test_novel_below_compare_floor(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    subject = "patient:us:below-floor"
    existing_text = "patient prefers tea in the morning"
    base = (await embedder.embed([existing_text]))[0]
    await seed_fact(
        db,
        tenant,
        subject_key=subject,
        text=existing_text,
        embedder=embedder,
        envelope=envelope,
    )
    other = (await embedder.embed(["something entirely unrelated"]))[0]
    candidate_vector = controlled_similarity_vector(base, other, cosine=0.5)
    chat = ScriptedChat([])

    async def run(cur):
        return await revise.classify(
            cur,
            chat,
            tenant,
            subject,
            candidate_text="a completely different claim",
            candidate_vector=candidate_vector,
            candidate_confidence=0.8,
            candidate_source_trust=SourceTrust.AGENT,
            envelope=envelope,
        )

    decision = await db.transaction(tenant, run, label="classify")
    assert decision.outcome is revise.Outcome.NOVEL
    assert chat.calls == [], "below COMPARE_FLOOR, the judge should never be asked"


async def test_reinforce_on_identical_text_and_compatible_judgment(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    subject = "patient:us:reinforce-test"
    text = "patient has a confirmed penicillin allergy"
    fact_id = await seed_fact(
        db, tenant, subject_key=subject, text=text, embedder=embedder, envelope=envelope
    )
    vector = (await embedder.embed([text]))[0]  # identical text -> cosine 1.0
    chat = ScriptedChat([{"contradictory": False, "reason": "same claim restated"}])

    async def run(cur):
        return await revise.classify(
            cur,
            chat,
            tenant,
            subject,
            candidate_text=text,
            candidate_vector=vector,
            candidate_confidence=0.85,
            candidate_source_trust=SourceTrust.AGENT,
            envelope=envelope,
        )

    decision = await db.transaction(tenant, run, label="classify")
    assert decision.outcome is revise.Outcome.REINFORCE
    assert decision.match is not None
    assert decision.match.fact_id == fact_id
    assert len(chat.calls) == 1


async def test_related_but_distinct_stays_novel(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    """Similar enough to compare, not similar enough to merge, and the judge
    says compatible: two related-but-different preferences should not become
    one row."""
    subject = "patient:us:related-not-dup"
    existing_text = "patient prefers tea"
    base = (await embedder.embed([existing_text]))[0]
    await seed_fact(
        db, tenant, subject_key=subject, text=existing_text, embedder=embedder, envelope=envelope
    )
    other = (await embedder.embed(["unrelated anchor text"]))[0]
    candidate_vector = controlled_similarity_vector(base, other, cosine=0.8)
    chat = ScriptedChat([{"contradictory": False, "reason": "a related but separate preference"}])

    async def run(cur):
        return await revise.classify(
            cur,
            chat,
            tenant,
            subject,
            candidate_text="patient prefers oolong tea specifically",
            candidate_vector=candidate_vector,
            candidate_confidence=0.8,
            candidate_source_trust=SourceTrust.AGENT,
            envelope=envelope,
        )

    decision = await db.transaction(tenant, run, label="classify")
    assert decision.outcome is revise.Outcome.NOVEL
    assert len(chat.calls) == 1, "still worth asking the judge above COMPARE_FLOOR"


async def test_supersede_new_wins_on_stronger_evidence(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    subject = "patient:us:supersede-new-wins"
    existing_text = "patient reports no known drug allergies"
    base = (await embedder.embed([existing_text]))[0]
    fact_id = await seed_fact(
        db,
        tenant,
        subject_key=subject,
        text=existing_text,
        embedder=embedder,
        envelope=envelope,
        trust=Trust.UNVERIFIED,
        confidence=0.4,
        strength=1.0,
        corroboration_count=0,
    )
    other = (await embedder.embed(["anchor"]))[0]
    candidate_vector = controlled_similarity_vector(base, other, cosine=0.85)
    chat = ScriptedChat([{"contradictory": True, "reason": "direct contradiction"}])

    async def run(cur):
        return await revise.classify(
            cur,
            chat,
            tenant,
            subject,
            candidate_text="patient confirmed a penicillin allergy, hives in 2019",
            candidate_vector=candidate_vector,
            candidate_confidence=0.95,
            candidate_source_trust=SourceTrust.OPERATOR,
            envelope=envelope,
        )

    decision = await db.transaction(tenant, run, label="classify")
    assert decision.outcome is revise.Outcome.SUPERSEDE
    assert decision.match is not None
    assert decision.match.fact_id == fact_id
    assert decision.new_wins is True


async def test_supersede_old_wins_when_existing_evidence_is_stronger(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    subject = "patient:us:supersede-old-wins"
    existing_text = "patient confirmed a penicillin allergy, hives in 2019"
    base = (await embedder.embed([existing_text]))[0]
    fact_id = await seed_fact(
        db,
        tenant,
        subject_key=subject,
        text=existing_text,
        embedder=embedder,
        envelope=envelope,
        trust=Trust.TRUSTED,
        confidence=0.95,
        strength=1.5,
        corroboration_count=3,
        source_event_id=uuid.uuid4(),  # migration 010 requires provenance to seed at 'trusted'
    )
    other = (await embedder.embed(["anchor"]))[0]
    candidate_vector = controlled_similarity_vector(base, other, cosine=0.85)
    chat = ScriptedChat([{"contradictory": True, "reason": "direct contradiction"}])

    async def run(cur):
        return await revise.classify(
            cur,
            chat,
            tenant,
            subject,
            candidate_text="patient has no drug allergies",
            candidate_vector=candidate_vector,
            candidate_confidence=0.5,
            candidate_source_trust=SourceTrust.AGENT,
            envelope=envelope,
        )

    decision = await db.transaction(tenant, run, label="classify")
    assert decision.outcome is revise.Outcome.SUPERSEDE
    assert decision.match is not None
    assert decision.match.fact_id == fact_id
    assert decision.new_wins is False


async def test_contest_when_evidence_is_comparable(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    subject = "patient:us:contest-test"
    existing_text = "patient prefers the north clinic location"
    base = (await embedder.embed([existing_text]))[0]
    fact_id = await seed_fact(
        db,
        tenant,
        subject_key=subject,
        text=existing_text,
        embedder=embedder,
        envelope=envelope,
        trust=Trust.UNVERIFIED,
        confidence=0.6,
        strength=1.0,
        corroboration_count=0,
    )
    other = (await embedder.embed(["anchor"]))[0]
    candidate_vector = controlled_similarity_vector(base, other, cosine=0.85)
    chat = ScriptedChat([{"contradictory": True, "reason": "conflicting preference"}])

    async def run(cur):
        return await revise.classify(
            cur,
            chat,
            tenant,
            subject,
            candidate_text="patient prefers the south clinic location",
            candidate_vector=candidate_vector,
            candidate_confidence=0.6,
            candidate_source_trust=SourceTrust.AGENT,
            envelope=envelope,
        )

    decision = await db.transaction(tenant, run, label="classify")
    assert decision.outcome is revise.Outcome.CONTEST
    assert decision.match is not None
    assert decision.match.fact_id == fact_id


async def test_unclear_judgment_never_reinforces(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    """An 'unclear' verdict must not be treated as compatible — a corroboration
    gate that can be talked into agreeing with itself by an uncertain judge is
    not a corroboration gate."""
    subject = "patient:us:unclear-test"
    existing_text = "patient prefers the north clinic location"
    base = (await embedder.embed([existing_text]))[0]
    await seed_fact(
        db,
        tenant,
        subject_key=subject,
        text=existing_text,
        embedder=embedder,
        envelope=envelope,
        confidence=0.6,
    )
    other = (await embedder.embed(["anchor"]))[0]
    candidate_vector = controlled_similarity_vector(base, other, cosine=0.95)
    chat = ScriptedChat([{"contradictory": None, "reason": "genuinely ambiguous"}])

    async def run(cur):
        return await revise.classify(
            cur,
            chat,
            tenant,
            subject,
            candidate_text="patient prefers a different clinic location",
            candidate_vector=candidate_vector,
            candidate_confidence=0.6,
            candidate_source_trust=SourceTrust.AGENT,
            envelope=envelope,
        )

    decision = await db.transaction(tenant, run, label="classify")
    assert decision.outcome is not revise.Outcome.REINFORCE
    assert decision.outcome in (revise.Outcome.CONTEST, revise.Outcome.SUPERSEDE)


def test_evidence_strength_rewards_confidence_trust_and_corroboration() -> None:
    baseline = revise.evidence_strength(
        confidence=0.5, trust_bonus=0.0, corroboration_count=0, strength=1.0
    )
    more_confident = revise.evidence_strength(
        confidence=0.9, trust_bonus=0.0, corroboration_count=0, strength=1.0
    )
    trusted = revise.evidence_strength(
        confidence=0.5, trust_bonus=0.4, corroboration_count=0, strength=1.0
    )
    corroborated = revise.evidence_strength(
        confidence=0.5, trust_bonus=0.0, corroboration_count=3, strength=1.0
    )
    assert more_confident > baseline
    assert trusted > baseline
    assert corroborated > baseline


def test_candidate_trust_bonus_matches_trusted_on_arrival() -> None:
    assert revise.candidate_trust_bonus(SourceTrust.SYSTEM) > 0
    assert revise.candidate_trust_bonus(SourceTrust.OPERATOR) > 0
    assert revise.candidate_trust_bonus(SourceTrust.AGENT) == 0.0
    assert revise.candidate_trust_bonus(SourceTrust.EXTERNAL) == 0.0
