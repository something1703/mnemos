"""Read-side residency enforcement — invariant 4, the direction that isn't
tested at the engine level (mnemos_engine tests cover the write-side refusal).

Three projection policies, three outcomes: `none` strips the fact, `aggregate`
also strips it (a single fact is never a valid aggregate view), `derived`
lets it through. Every outcome is logged, permitted or not.

Two independent axes throughout: the WRITE happens from the subject's real
home region (satisfying the write-side check tested at the engine level), and
the READ is simulated from a different requester region (the axis this file
actually tests). Conflating the two was the first version of this file's bug.
"""

from __future__ import annotations

import uuid

import pytest
from mnemos_engine.errors import ResidencyViolation
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Op, SourceTrust
from mnemos_warden.residency import assert_episode_readable, enforce_recall_projection, where_is

pytestmark = pytest.mark.invariant


async def _set_policy(db, tenant, pattern, region, projection):
    async def run(cur):
        await append_audit(cur, tenant, op=Op.POLICY, actor="test", subject_key=pattern)
        await cur.execute(
            "INSERT INTO mnemos.residency_policies "
            "(tenant_id, subject_pattern, home_region, projection) VALUES (%s, %s, %s, %s)",
            (tenant, pattern, region, projection),
        )

    await db.transaction(tenant, run, label="set_policy")


async def _write_fact(db, engine, tenant, subject, text):
    """Write through `engine` (already constructed for the subject's home
    region) so the write-side residency check passes, then attach a fact with
    a real provenance edge."""
    from mnemos_engine.embeddings import FakeEmbedder, to_pgvector

    episode = await engine.remember(
        tenant,
        subject_key=subject,
        session_id=uuid.uuid4(),
        event_type="note",
        content=text,
        source_trust=SourceTrust.OPERATOR,
    )

    embedder = FakeEmbedder()
    vector = (await embedder.embed([text]))[0]
    fact_id = uuid.uuid4()

    async def run(cur):
        ciphertext, wrapped = engine.envelope.encrypt(text, aad=f"mnemos:{tenant}:{subject}")
        await append_audit(cur, tenant, op=Op.CONSOLIDATE, actor="test", subject_key=subject)
        # Invariant 3: a fact cannot be inserted directly at a recallable
        # trust state with no provenance edge yet. Insert unverified, attach
        # the edge, then promote — the order the real sleep cycle uses.
        await cur.execute(
            """
            INSERT INTO mnemos.semantic_facts
                (tenant_id, fact_id, home_region, subject_key, fact_kind,
                 text_ciphertext, text_dek_wrapped, text_hash, embedding, tsv, trust)
            VALUES (%s, %s, %s, %s, 'note', %s, %s, %s, %s, to_tsvector(%s), 'unverified')
            """,
            (
                tenant,
                fact_id,
                episode.home_region,
                subject,
                ciphertext,
                wrapped,
                b"\x00" * 32,
                to_pgvector(vector),
                text,
            ),
        )
        await cur.execute(
            "INSERT INTO mnemos.fact_provenance (tenant_id, fact_id, event_id, subject_key) "
            "VALUES (%s, %s, %s, %s)",
            (tenant, fact_id, episode.event_id, subject),
        )
        await cur.execute(
            "UPDATE mnemos.semantic_facts SET trust = 'trusted' "
            "WHERE tenant_id = %s AND fact_id = %s",
            (tenant, fact_id),
        )

    await db.transaction(tenant, run, label="make_fact")
    return episode, fact_id


async def test_none_projection_strips_the_fact_and_logs_the_denial(
    db, engine_for, tenant: uuid.UUID
) -> None:
    await _set_policy(db, tenant, "patient:eu:*", "eu-central-1", "none")
    eu_engine = engine_for(tenant, region="eu-central-1")
    text = "EU patient allergy record."
    await _write_fact(db, eu_engine, tenant, "patient:eu:900", text)

    result = await eu_engine.recall(tenant, text, subject_key="patient:eu:900")
    assert result.facts, "must be recallable pre-filter to make the test meaningful"

    async def filtered(cur):
        return await enforce_recall_projection(
            cur, tenant, result, requester_region="us-east-1", requested_by="test:api"
        )

    filtered_result = await db.transaction(tenant, filtered, label="filter")
    assert filtered_result.facts == []

    async def crossings(cur):
        await cur.execute(
            "SELECT allowed, denied_reason FROM mnemos.region_crossings WHERE tenant_id = %s",
            (tenant,),
        )
        return await cur.fetchall()

    rows = await db.transaction(tenant, crossings, label="read")
    assert rows and rows[0][0] is False


async def test_aggregate_projection_also_strips_an_individual_fact(
    db, engine_for, tenant: uuid.UUID
) -> None:
    """A single ScoredFact is never a valid aggregate view — aggregate-only
    policy must withhold it just as strictly as `none`."""
    await _set_policy(db, tenant, "staff:*", "eu-central-1", "aggregate")
    eu_engine = engine_for(tenant, region="eu-central-1")
    text = "Staff scheduling note."
    await _write_fact(db, eu_engine, tenant, "staff:900", text)

    result = await eu_engine.recall(tenant, text, subject_key="staff:900")
    assert result.facts

    async def filtered(cur):
        return await enforce_recall_projection(
            cur, tenant, result, requester_region="us-east-1", requested_by="test:api"
        )

    filtered_result = await db.transaction(tenant, filtered, label="filter")
    assert filtered_result.facts == []


async def test_derived_projection_permits_the_fact_and_logs_the_crossing(
    db, engine_for, tenant: uuid.UUID
) -> None:
    await _set_policy(db, tenant, "patient:in:*", "ap-south-1", "derived")
    in_engine = engine_for(tenant, region="ap-south-1")
    text = "Derived-eligible patient note."
    await _write_fact(db, in_engine, tenant, "patient:in:900", text)

    result = await in_engine.recall(tenant, text, subject_key="patient:in:900")
    assert result.facts

    async def filtered(cur):
        return await enforce_recall_projection(
            cur, tenant, result, requester_region="us-east-1", requested_by="test:api"
        )

    filtered_result = await db.transaction(tenant, filtered, label="filter")
    assert len(filtered_result.facts) == 1

    async def crossings(cur):
        await cur.execute(
            "SELECT allowed FROM mnemos.region_crossings WHERE tenant_id = %s", (tenant,)
        )
        return [r[0] for r in await cur.fetchall()]

    assert await db.transaction(tenant, crossings, label="read") == [True]


async def test_same_region_request_needs_no_policy_lookup(
    db, engine_for, tenant: uuid.UUID
) -> None:
    """The common case: a request from the fact's own region is served
    without consulting residency policy at all, and without logging a
    crossing that never happened."""
    engine = engine_for(tenant, region="us-east-1")
    text = "Local note, no cross-border question here."
    await _write_fact(db, engine, tenant, "local:1", text)

    result = await engine.recall(tenant, text, subject_key="local:1")
    assert result.facts

    async def filtered(cur):
        return await enforce_recall_projection(
            cur, tenant, result, requester_region="us-east-1", requested_by="test:api"
        )

    filtered_result = await db.transaction(tenant, filtered, label="filter")
    assert len(filtered_result.facts) == 1

    async def crossings(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.region_crossings WHERE tenant_id = %s", (tenant,)
        )
        return (await cur.fetchone())[0]

    assert await db.transaction(tenant, crossings, label="read") == 0


async def test_raw_episode_content_never_crosses_regardless_of_projection(
    db, engine_for, tenant: uuid.UUID
) -> None:
    """Projections apply to derived facts. Raw episode content never crosses
    a border, even under a `derived` policy that would let the corresponding
    fact through."""
    await _set_policy(db, tenant, "patient:in:*", "ap-south-1", "derived")
    in_engine = engine_for(tenant, region="ap-south-1")
    await _write_fact(db, in_engine, tenant, "patient:in:901", "Raw content test.")

    async def check(cur):
        await assert_episode_readable(
            cur,
            tenant,
            "patient:in:901",
            requester_region="us-east-1",
            requested_by="test:api",
        )

    with pytest.raises(ResidencyViolation):
        await db.transaction(tenant, check, label="check")


async def test_where_is_reports_physical_region_and_governing_policy(
    db, engine_for, tenant: uuid.UUID
) -> None:
    await _set_policy(db, tenant, "patient:eu:*", "eu-central-1", "derived")
    eu_engine = engine_for(tenant, region="eu-central-1")
    await _write_fact(db, eu_engine, tenant, "patient:eu:where", "Where-is test.")

    async def run(cur):
        return await where_is(cur, tenant, "patient:eu:where")

    report = await db.transaction(tenant, run, label="where_is", read_only=True)
    assert report.episode_regions
    assert report.fact_regions
    assert report.governing_policy == "patient:eu:*"
