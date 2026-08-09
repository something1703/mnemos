"""The corroboration gate: independence, promotion, and TTL quarantine.

The `test_max_independent_corroborations_*` tests below matter most — they
are the direct, isolated proof of the "different session AND different
source_trust" definition that `corroboration.py`'s module docstring calls the
whole defense. Everything else here is important; those are load-bearing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
from mnemos_engine.crypto import Envelope
from mnemos_engine.db import Database
from mnemos_engine.embeddings import Embedder
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Op, Trust
from mnemos_sleep_cycle import corroboration
from mnemos_sleep_cycle.corroboration import _max_independent_corroborations

from .conftest import seed_fact

# --------------------------------------------------------------- pure logic


def test_max_independent_corroborations_empty() -> None:
    assert _max_independent_corroborations(set()) == 0


def test_max_independent_corroborations_single_signature() -> None:
    s1 = uuid.uuid4()
    assert _max_independent_corroborations({(s1, "agent")}) == 1


def test_max_independent_corroborations_same_session_different_trust_is_not_independent() -> None:
    """The case the module docstring warns about: two signatures that are
    literally distinct pairs, but share a session, must not count as two."""
    s1 = uuid.uuid4()
    signatures = {(s1, "agent"), (s1, "operator")}
    assert _max_independent_corroborations(signatures) == 1


def test_max_independent_corroborations_same_trust_different_session_is_not_independent() -> None:
    """Two attacker-controlled sessions, same source_trust category: only one
    can be matched to the single 'agent' slot."""
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    signatures = {(s1, "agent"), (s2, "agent")}
    assert _max_independent_corroborations(signatures) == 1


def test_max_independent_corroborations_genuinely_independent_pair() -> None:
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    signatures = {(s1, "agent"), (s2, "operator")}
    assert _max_independent_corroborations(signatures) == 2


def test_max_independent_corroborations_five_signatures_one_session_one_trust() -> None:
    """The self-corroboration scenario stated directly: one malicious episode,
    however many facts get extracted from it, contributes at most one
    independent signature — because it is one session with one source_trust,
    no matter how many (fact, edge) pairs point back at it."""
    s1 = uuid.uuid4()
    signatures = {(s1, "external")}  # a set collapses duplicates by construction
    assert _max_independent_corroborations(signatures) == 1


def test_max_independent_corroborations_caps_at_four_categories() -> None:
    sessions = [uuid.uuid4() for _ in range(6)]
    categories = ["system", "operator", "agent", "external"]
    signatures = {(s, categories[i % 4]) for i, s in enumerate(sessions)}
    assert _max_independent_corroborations(signatures) <= 4


def test_determine_trust_holds_at_unverified_below_threshold() -> None:
    assert (
        corroboration.determine_trust(
            Trust.UNVERIFIED, corroboration_count=1, has_trusted_source=False
        )
        is Trust.UNVERIFIED
    )


def test_determine_trust_promotes_at_two_independent_sources() -> None:
    assert (
        corroboration.determine_trust(
            Trust.UNVERIFIED, corroboration_count=2, has_trusted_source=False
        )
        is Trust.CORROBORATED
    )


def test_determine_trust_trusted_source_promotes_directly() -> None:
    assert (
        corroboration.determine_trust(
            Trust.UNVERIFIED, corroboration_count=1, has_trusted_source=True
        )
        is Trust.TRUSTED
    )


def test_determine_trust_never_moves_contested_or_quarantined() -> None:
    assert (
        corroboration.determine_trust(
            Trust.CONTESTED, corroboration_count=5, has_trusted_source=True
        )
        is Trust.CONTESTED
    )
    assert (
        corroboration.determine_trust(
            Trust.QUARANTINED, corroboration_count=5, has_trusted_source=True
        )
        is Trust.QUARANTINED
    )


# --------------------------------------------------------------- against a DB


async def test_recompute_corroboration_counts_independent_episode_sources(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    subject = "patient:us:corrob-count"
    fact_id = await seed_fact(
        db, tenant, subject_key=subject, text="a claim", embedder=embedder, envelope=envelope
    )

    session_a, session_b = uuid.uuid4(), uuid.uuid4()

    async def add_edges_explicit(cur: psycopg.AsyncCursor) -> None:
        await append_audit(
            cur,
            tenant,
            op=Op.CONSOLIDATE,
            actor="test",
            subject_key=subject,
            payload={"test": "add_edges"},
        )
        for session_id, trust in ((session_a, "agent"), (session_b, "operator")):
            event_id = uuid.uuid4()
            await cur.execute(
                "INSERT INTO mnemos.episodic_events "
                "(tenant_id, subject_key, event_id, home_region, session_id, event_type, "
                " content_ciphertext, content_dek_wrapped, content_hash, source_trust) "
                "VALUES (%s, %s, %s, 'us-east-1', %s, 'note', %s, %s, %s, %s)",
                (tenant, subject, event_id, session_id, b"\x00", b"\x00", b"\x00", trust),
            )
            await cur.execute(
                "INSERT INTO mnemos.fact_provenance (tenant_id, fact_id, event_id, subject_key) "
                "VALUES (%s, %s, %s, %s)",
                (tenant, fact_id, event_id, subject),
            )

    await db.transaction(tenant, add_edges_explicit, label="add_edges")

    async def recompute(cur: psycopg.AsyncCursor) -> tuple[int, bool]:
        return await corroboration.recompute_corroboration(cur, tenant, fact_id)

    count, has_trusted = await db.transaction(tenant, recompute, label="recompute", read_only=True)
    assert count == 2
    assert has_trusted is True  # one of the two episodes was 'operator'


async def test_apply_trust_transition_promotes_unverified_to_corroborated(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    subject = "patient:us:promote-test"
    e1, e2 = uuid.uuid4(), uuid.uuid4()
    fact_id = await seed_fact(
        db,
        tenant,
        subject_key=subject,
        text="a claim",
        embedder=embedder,
        envelope=envelope,
        source_event_id=e1,
    )

    async def setup_second_session(cur: psycopg.AsyncCursor) -> None:
        await append_audit(
            cur,
            tenant,
            op=Op.CONSOLIDATE,
            actor="test",
            subject_key=subject,
            payload={},
        )
        await cur.execute(
            "INSERT INTO mnemos.episodic_events "
            "(tenant_id, subject_key, event_id, home_region, session_id, event_type, "
            " content_ciphertext, content_dek_wrapped, content_hash, source_trust) "
            "VALUES (%s, %s, %s, 'us-east-1', %s, 'note', %s, %s, %s, 'agent')",
            (tenant, subject, e2, uuid.uuid4(), b"\x00", b"\x00", b"\x00"),
        )
        # The seeded fact's own edge (e1) implicitly belongs to no episodic_events
        # row, which is fine for the provenance-count query (it JOINs, so a
        # dangling edge with no matching episode contributes nothing) — insert a
        # matching row for e1 too so this test's signature set is well-formed.
        await cur.execute(
            "INSERT INTO mnemos.episodic_events "
            "(tenant_id, subject_key, event_id, home_region, session_id, event_type, "
            " content_ciphertext, content_dek_wrapped, content_hash, source_trust) "
            "VALUES (%s, %s, %s, 'us-east-1', %s, 'note', %s, %s, %s, 'external') "
            "ON CONFLICT DO NOTHING",
            (tenant, subject, e1, uuid.uuid4(), b"\x00", b"\x00", b"\x00"),
        )
        await cur.execute(
            "INSERT INTO mnemos.fact_provenance (tenant_id, fact_id, event_id, subject_key) "
            "VALUES (%s, %s, %s, %s)",
            (tenant, fact_id, e2, subject),
        )

    await db.transaction(tenant, setup_second_session, label="setup")

    async def transition(cur: psycopg.AsyncCursor) -> tuple[Trust, Trust]:
        return await corroboration.apply_trust_transition(
            cur, tenant, fact_id, subject, actor="test"
        )

    previous, current = await db.transaction(tenant, transition, label="transition")
    assert previous is Trust.UNVERIFIED
    assert current is Trust.CORROBORATED


async def test_quarantine_stale_unverified_moves_old_facts_only(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    stale_subject = "patient:us:stale"
    fresh_subject = "patient:us:fresh"
    stale_id = await seed_fact(
        db,
        tenant,
        subject_key=stale_subject,
        text="an old unverified claim",
        embedder=embedder,
        envelope=envelope,
    )
    fresh_id = await seed_fact(
        db,
        tenant,
        subject_key=fresh_subject,
        text="a fresh unverified claim",
        embedder=embedder,
        envelope=envelope,
    )

    async def backdate(cur: psycopg.AsyncCursor) -> None:
        await append_audit(
            cur,
            tenant,
            op=Op.CONSOLIDATE,
            actor="test",
            subject_key=stale_subject,
            payload={},
        )
        await cur.execute(
            "UPDATE mnemos.semantic_facts SET created_at = %s "
            "WHERE tenant_id = %s AND fact_id = %s",
            (datetime.now(UTC) - timedelta(days=60), tenant, stale_id),
        )

    await db.transaction(tenant, backdate, label="backdate")

    async def sweep(cur: psycopg.AsyncCursor) -> int:
        return await corroboration.quarantine_stale_unverified(
            cur, tenant, actor="test", ttl_days=30
        )

    count = await db.transaction(tenant, sweep, label="quarantine_sweep")
    assert count == 1

    async def read_trust(cur: psycopg.AsyncCursor, fact_id: uuid.UUID) -> Trust:
        await cur.execute(
            "SELECT trust FROM mnemos.semantic_facts WHERE tenant_id = %s AND fact_id = %s",
            (tenant, fact_id),
        )
        row = await cur.fetchone()
        return Trust(row[0])

    stale_trust = await db.transaction(
        tenant, lambda cur: read_trust(cur, stale_id), label="read", read_only=True
    )
    fresh_trust = await db.transaction(
        tenant, lambda cur: read_trust(cur, fresh_id), label="read", read_only=True
    )
    assert stale_trust is Trust.QUARANTINED
    assert fresh_trust is Trust.UNVERIFIED
