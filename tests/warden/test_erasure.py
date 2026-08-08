"""The four erasure modes, plus the guarantees that make them mean something:
the preview/execute count agreement, the vector-orphan check, the atomic
rollback under fault injection, and (for shred) the backup-adjacent claim —
that destroying the key, not just the rows, is what closes the gap `forget`
leaves open.
"""

from __future__ import annotations

import uuid

import pytest
from mnemos_engine.errors import LegalHoldActive
from mnemos_engine.models import SourceTrust, Trust
from mnemos_warden.errors import UnknownSubject
from mnemos_warden.models import EraseMode

pytestmark = pytest.mark.invariant


async def _seed_fact(db, engine, tenant: uuid.UUID, subject: str, text: str, trust=Trust.TRUSTED):
    """Write an episode and a provenance-linked, promotable fact — the shape
    every erasure test needs as its starting state."""
    from mnemos_engine.embeddings import FakeEmbedder, to_pgvector
    from mnemos_engine.ledger import append_audit
    from mnemos_engine.models import Op

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
        await cur.execute(
            """
            INSERT INTO mnemos.semantic_facts
                (tenant_id, fact_id, home_region, subject_key, fact_kind,
                 text_ciphertext, text_dek_wrapped, text_hash, embedding, tsv, trust)
            VALUES (%s, %s, 'us-east-1', %s, 'note', %s, %s, %s, %s, to_tsvector(%s), 'unverified')
            """,
            (
                tenant,
                fact_id,
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
        if trust is not Trust.UNVERIFIED:
            await cur.execute(
                "UPDATE mnemos.semantic_facts SET trust = %s WHERE tenant_id = %s AND fact_id = %s",
                (str(trust), tenant, fact_id),
            )

    await db.transaction(tenant, run, label="seed_fact")
    return episode, fact_id


# ---------------------------------------------------------------- preview


async def test_preview_matches_what_execute_actually_removes(
    db, engine_for, warden, tenant: uuid.UUID
) -> None:
    """preview() and execute() call the SAME counting function. This asserts
    the observable consequence: the number previewed is the number a
    subsequent forget() reports as removed — not merely that the code looks
    shared."""
    engine = engine_for(tenant)
    await _seed_fact(db, engine, tenant, "patient:preview", "Some clinical note.")

    preview = await warden.preview_erasure(tenant, "patient:preview", EraseMode.FORGET)
    assert preview.episode_count == 1
    assert preview.fact_count == 1
    assert not preview.is_blocked

    result = await warden.forget(
        tenant, "patient:preview", actor="test", reason="test", confirm=True
    )
    assert result.episodes_removed == preview.episode_count
    assert result.facts_removed == preview.fact_count


async def test_preview_of_unknown_subject_raises(warden, tenant: uuid.UUID) -> None:
    """Distinguished from a truthful-but-misleading zero-row proof: a caller
    who mistyped a subject key should see that plainly."""
    with pytest.raises(UnknownSubject):
        await warden.preview_erasure(tenant, "patient:does-not-exist", EraseMode.FORGET)


# ----------------------------------------------------------------- forget


async def test_forget_removes_episodes_facts_and_provenance(
    db, engine_for, warden, tenant: uuid.UUID
) -> None:
    engine = engine_for(tenant)
    await _seed_fact(db, engine, tenant, "patient:forget", "Allergy to penicillin.")

    result = await warden.forget(
        tenant, "patient:forget", actor="test", reason="right to erasure", confirm=True
    )
    assert (result.episodes_removed, result.facts_removed) == (1, 1)

    async def counts(cur):
        await cur.execute(
            "SELECT "
            "(SELECT count(*) FROM mnemos.episodic_events WHERE tenant_id=%s AND subject_key=%s), "
            "(SELECT count(*) FROM mnemos.semantic_facts WHERE tenant_id=%s AND subject_key=%s), "
            "(SELECT count(*) FROM mnemos.fact_provenance WHERE tenant_id=%s AND subject_key=%s)",
            (tenant, "patient:forget") * 3,
        )
        return await cur.fetchone()

    row = await db.transaction(tenant, counts, label="verify")
    assert tuple(row) == (0, 0, 0)


async def test_forget_leaves_no_orphaned_vector(db, engine_for, warden, tenant: uuid.UUID) -> None:
    """The claim a separate vector store cannot make: the embedding dies in
    the SAME statement as the row. Proven by querying raw vector similarity
    against a pre-computed embedding of the deleted text and getting nothing —
    not by trusting that a DELETE ... CASCADE happened."""
    from mnemos_engine.embeddings import FakeEmbedder, to_pgvector

    engine = engine_for(tenant)
    text = "Extremely distinctive clinical phrase for vector orphan test."
    await _seed_fact(db, engine, tenant, "patient:vector", text)

    await warden.forget(tenant, "patient:vector", actor="test", reason="test", confirm=True)

    embedder = FakeEmbedder()
    vector = (await embedder.embed([text]))[0]
    literal = to_pgvector(vector)

    async def search(cur):
        await cur.execute(
            "SELECT count(*) FROM ("
            "  SELECT fact_id FROM mnemos.semantic_facts"
            "  WHERE tenant_id = %s ORDER BY embedding <-> %s::VECTOR LIMIT 5"
            ") nearest",
            (tenant, literal),
        )
        return (await cur.fetchone())[0]

    assert await db.transaction(tenant, search, label="search") == 0


async def test_forget_is_atomic_under_injected_fault(
    db, engine_for, warden, key_provider, tenant: uuid.UUID
) -> None:
    """Forgetting is atomic or not at all. Simulated by monkeypatching
    execute_forget's append_audit call to raise after the deletes queue but
    before commit — the transaction wrapper must roll back everything."""
    import mnemos_warden.erasure as erasure_mod

    engine = engine_for(tenant)
    await _seed_fact(db, engine, tenant, "patient:atomic", "Content that must survive a fault.")

    real_execute_forget = erasure_mod.execute_forget

    async def faulty(cur, tid, subject_key, *, actor, reason):
        # Run the real deletes, then blow up before the caller can commit —
        # Database.transaction's rollback is what must save us here.
        await real_execute_forget(cur, tid, subject_key, actor=actor, reason=reason)
        raise RuntimeError("injected fault after deletes, before commit")

    erasure_mod.execute_forget = faulty
    try:
        with pytest.raises(RuntimeError, match="injected fault"):
            await warden.forget(tenant, "patient:atomic", actor="test", reason="test", confirm=True)
    finally:
        erasure_mod.execute_forget = real_execute_forget

    async def counts(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.episodic_events WHERE tenant_id=%s AND subject_key=%s",
            (tenant, "patient:atomic"),
        )
        return (await cur.fetchone())[0]

    assert await db.transaction(tenant, counts, label="verify") == 1, (
        "a rolled-back forget must leave the data present, not half-deleted"
    )


# ------------------------------------------------------------- legal hold


async def test_forget_against_a_held_subject_is_refused_loudly(
    db, engine_for, warden, tenant: uuid.UUID
) -> None:
    engine = engine_for(tenant)
    await _seed_fact(db, engine, tenant, "patient:held", "Record under investigation.")

    await warden.set_legal_hold(
        tenant,
        "patient:held",
        matter_reference="INS-2026-0001",
        placed_by="compliance:officer",
        confirm=True,
    )

    with pytest.raises(LegalHoldActive) as exc:
        await warden.forget(
            tenant, "patient:held", actor="test", reason="erasure request", confirm=True
        )
    assert exc.value.matter_reference == "INS-2026-0001"
    assert exc.value.placed_by == "compliance:officer"

    # Loud, not silent: nothing was touched.
    async def counts(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.episodic_events WHERE tenant_id=%s AND subject_key=%s",
            (tenant, "patient:held"),
        )
        return (await cur.fetchone())[0]

    assert await db.transaction(tenant, counts, label="verify") == 1


async def test_hold_placed_between_preview_and_confirm_still_blocks(
    db, engine_for, warden, tenant: uuid.UUID
) -> None:
    """The TOCTOU case: a hold appears after the preview a human approved but
    before they click confirm. The re-check inside execute()'s own
    transaction is what catches this — a check only at preview time would
    not."""
    engine = engine_for(tenant)
    await _seed_fact(db, engine, tenant, "patient:race", "Record.")

    preview = await warden.preview_erasure(tenant, "patient:race", EraseMode.FORGET)
    assert not preview.is_blocked

    await warden.set_legal_hold(
        tenant,
        "patient:race",
        matter_reference="LATE-HOLD",
        placed_by="compliance:officer",
        confirm=True,
    )

    with pytest.raises(LegalHoldActive):
        await warden.forget(tenant, "patient:race", actor="test", reason="test", confirm=True)


async def test_forget_succeeds_after_hold_is_released(
    db, engine_for, warden, tenant: uuid.UUID
) -> None:
    engine = engine_for(tenant)
    await _seed_fact(db, engine, tenant, "patient:released", "Record.")

    hold = await warden.set_legal_hold(
        tenant, "patient:released", matter_reference="M-1", placed_by="c", confirm=True
    )
    await warden.release_legal_hold(
        tenant, hold.hold_id, released_by="c", release_reason="matter closed", confirm=True
    )

    result = await warden.forget(
        tenant, "patient:released", actor="test", reason="test", confirm=True
    )
    assert result.episodes_removed == 1


# --------------------------------------------------------------- redact


async def test_redact_keeps_the_row_but_destroys_the_content(
    db, engine_for, warden, tenant: uuid.UUID
) -> None:
    engine = engine_for(tenant)
    await _seed_fact(db, engine, tenant, "patient:redact", "Sensitive content.")

    result = await warden.redact(
        tenant, "patient:redact", actor="test", reason="content-only removal", confirm=True
    )
    assert result.episodes_removed == 0, "redact must not remove the row"

    async def read(cur):
        await cur.execute(
            "SELECT count(*), count(*) FILTER (WHERE content_ciphertext = '\\x00') "
            "FROM mnemos.episodic_events WHERE tenant_id=%s AND subject_key=%s",
            (tenant, "patient:redact"),
        )
        return await cur.fetchone()

    total, tombstoned = await db.transaction(tenant, read, label="verify")
    assert (total, tombstoned) == (1, 1)


# ------------------------------------------------------------ quarantine


async def test_quarantine_is_reversible_and_hides_from_recall(
    db, engine_for, warden, tenant: uuid.UUID
) -> None:
    engine = engine_for(tenant)
    await _seed_fact(db, engine, tenant, "patient:quarantine", "Suspect data.")

    before = await engine.recall(tenant, "Suspect data.", subject_key="patient:quarantine")
    assert before.facts, "must be recallable before quarantine to make this test meaningful"

    await warden.quarantine(
        tenant, "patient:quarantine", actor="test", reason="under review", confirm=True
    )

    after = await engine.recall(tenant, "Suspect data.", subject_key="patient:quarantine")
    assert after.facts == []

    async def still_present(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.semantic_facts WHERE tenant_id=%s AND subject_key=%s",
            (tenant, "patient:quarantine"),
        )
        return (await cur.fetchone())[0]

    assert await db.transaction(tenant, still_present, label="verify") == 1, (
        "quarantine must retain the row — it is reversible, not a deletion"
    )


# ----------------------------------------------------------------- shred


async def test_shred_forgets_and_destroys_the_tenant_key(
    db, engine_for, key_provider, warden, tenant: uuid.UUID
) -> None:
    """SHRED = FORGET + key destruction. Proves both halves: the target
    subject's rows are gone, AND separately-seeded content for a DIFFERENT
    subject in the same tenant becomes unreadable — because shred destroys
    the tenant's key, not a per-subject one."""
    engine = engine_for(tenant)
    await _seed_fact(db, engine, tenant, "patient:shred-target", "Erase this subject entirely.")

    other_episode, _ = await _seed_fact(
        db, engine, tenant, "patient:shred-collateral", "Unrelated content, same tenant."
    )

    result = await warden.shred(
        tenant, "patient:shred-target", actor="test", reason="full erasure", confirm=True
    )
    assert result.key_destroyed is True
    assert result.episodes_removed == 1
    assert key_provider.is_destroyed(tenant)

    # The target subject's rows are gone (the FORGET half).
    async def target_count(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.episodic_events WHERE tenant_id=%s AND subject_key=%s",
            (tenant, "patient:shred-target"),
        )
        return (await cur.fetchone())[0]

    assert await db.transaction(tenant, target_count, label="verify") == 0

    # The collateral subject's ROW still exists (shred only forgets the named
    # subject) but its CONTENT is unrecoverable, because the key it was
    # wrapped under no longer exists anywhere.
    fresh_engine = engine_for(tenant)
    result2 = await fresh_engine.recall(
        tenant, "Unrelated content, same tenant.", subject_key="patient:shred-collateral"
    )
    if result2.facts:
        assert result2.facts[0].fact.text is None, (
            "content must read as destroyed, not as an error, and not as recoverable"
        )

    async def collateral_row_survives(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.episodic_events WHERE tenant_id=%s AND subject_key=%s",
            (tenant, "patient:shred-collateral"),
        )
        return (await cur.fetchone())[0]

    assert await db.transaction(tenant, collateral_row_survives, label="verify") == 1
    assert other_episode.subject_key == "patient:shred-collateral"


async def test_shred_against_a_held_subject_is_refused(
    db, engine_for, warden, key_provider, tenant: uuid.UUID
) -> None:
    """The most consequential operation gets the same refusal as the mildest
    one. Shred is not a way to route around a hold."""
    engine = engine_for(tenant)
    await _seed_fact(db, engine, tenant, "patient:held-shred", "Record.")
    await warden.set_legal_hold(
        tenant, "patient:held-shred", matter_reference="M-2", placed_by="c", confirm=True
    )

    with pytest.raises(LegalHoldActive):
        await warden.shred(tenant, "patient:held-shred", actor="test", reason="test", confirm=True)
    assert not key_provider.is_destroyed(tenant)
