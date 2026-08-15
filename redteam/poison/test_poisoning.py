"""Attack class 1 — memory poisoning (PHASE_10 10.1).

Every test here assumes the attacker has already won the prompt layer: the
distiller is `CompromisedDistiller`, which emits the attacker's exact claim at
confidence 1.0 and tells the contradiction judge everything agrees. Nothing
below depends on a model refusing anything.

The question each test asks is the one the threat model actually cares about:
with the model fully owned, can the attacker reach one of PHASE_10 10.1's
three success conditions — a `trusted` fact, an executable skill, or a Warden
operation? The answer must be no, and it must be no for a structural reason
that is named in the assertion.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from mnemos_engine.crypto import Envelope
from mnemos_engine.db import Database
from mnemos_engine.embeddings import Embedder
from mnemos_engine.engine import MnemosEngine
from mnemos_engine.models import SourceTrust, Trust
from mnemos_sleep_cycle.consolidate import SessionBatch, consolidate_batch

from ..conftest import CompromisedDistiller
from .payloads import ALL_PAYLOADS, ATTACKER_CLAIM, MULTI_TURN, SINGLE_EPISODE_PAYLOADS, Payload

pytestmark = [pytest.mark.security, pytest.mark.redteam]


async def _facts(db: Database, tenant_id: uuid.UUID, subject: str) -> list[tuple]:
    async def run(cur: psycopg.AsyncCursor) -> list[tuple]:
        await cur.execute(
            "SELECT trust, corroboration_count FROM mnemos.semantic_facts "
            "WHERE tenant_id = %s AND subject_key = %s",
            (tenant_id, subject),
        )
        return await cur.fetchall()

    return await db.transaction(tenant_id, run, label="redteam_facts", read_only=True)


async def _run_attack(
    *,
    db: Database,
    engine: MnemosEngine,
    embedder: Embedder,
    envelope: Envelope,
    tenant_id: uuid.UUID,
    subject: str,
    contents: list[str],
    source_trust: SourceTrust,
    session_id: uuid.UUID | None = None,
) -> None:
    """Write the payload episodes and consolidate them, exactly as the real
    pipeline would, with a distiller that is on the attacker's side."""
    session = session_id or uuid.uuid4()
    for content in contents:
        await engine.remember(
            tenant_id,
            subject_key=subject,
            session_id=session,
            event_type="note",
            content=content,
            source_trust=source_trust,
        )

    chat = CompromisedDistiller(ATTACKER_CLAIM)
    batch = SessionBatch(
        tenant_id=tenant_id,
        session_id=session,
        subject_key=subject,
        home_region="us-east-1",
        episode_count=len(contents),
    )

    async def run(cur: psycopg.AsyncCursor):
        return await consolidate_batch(
            cur, batch, chat=chat, embedder=embedder, envelope=envelope, actor="redteam"
        )

    await db.transaction(tenant_id, run, label="redteam_consolidate")


@pytest.mark.parametrize("payload", SINGLE_EPISODE_PAYLOADS, ids=lambda p: p.technique)
async def test_single_source_injection_never_reaches_trusted(
    payload: Payload,
    db: Database,
    redteam_tenant: uuid.UUID,
    engine: MnemosEngine,
    embedder: Embedder,
    envelope: Envelope,
) -> None:
    """One attacker-controlled source, six techniques, a fully compromised
    distiller — and the claim still lands `unverified`.

    Not because the payload was detected. It was not: the fact says exactly
    what the attacker wanted. It stays untrusted because promotion needs two
    independent provenance signatures and the attacker has one, which is
    arithmetic rather than judgement.
    """
    subject = f"patient:redteam-{uuid.uuid4().hex[:8]}"
    await _run_attack(
        db=db,
        engine=engine,
        embedder=embedder,
        envelope=envelope,
        tenant_id=redteam_tenant,
        subject=subject,
        contents=[payload.content],
        source_trust=SourceTrust.EXTERNAL,
    )

    rows = await _facts(db, redteam_tenant, subject)
    assert rows, "the attack should produce a fact — being blocked here would be luck, not design"
    for trust, corroboration in rows:
        assert Trust(trust) is Trust.UNVERIFIED, f"{payload.technique} promoted a fact"
        assert corroboration == 1


async def test_multi_turn_split_payload_never_reaches_trusted(
    db: Database,
    redteam_tenant: uuid.UUID,
    engine: MnemosEngine,
    embedder: Embedder,
    envelope: Envelope,
) -> None:
    """Three innocuous episodes that only become an instruction when read
    together — which is precisely what consolidation does.

    They share a session, so they are one provenance signature no matter how
    many turns the attacker spreads the payload across. Splitting a payload
    buys nothing against a gate that counts sources rather than messages.
    """
    subject = f"patient:redteam-{uuid.uuid4().hex[:8]}"
    await _run_attack(
        db=db,
        engine=engine,
        embedder=embedder,
        envelope=envelope,
        tenant_id=redteam_tenant,
        subject=subject,
        contents=[p.content for p in MULTI_TURN],
        source_trust=SourceTrust.EXTERNAL,
    )

    rows = await _facts(db, redteam_tenant, subject)
    for trust, corroboration in rows:
        assert Trust(trust) is Trust.UNVERIFIED
        assert corroboration == 1, "three turns of one session are one source"


async def test_repeating_the_payload_in_new_sessions_does_not_promote_it(
    db: Database,
    redteam_tenant: uuid.UUID,
    engine: MnemosEngine,
    embedder: Embedder,
    envelope: Envelope,
) -> None:
    """The obvious next move: say it again, from a fresh session, five times.

    Independence needs a different session AND a different `source_trust`
    category. An attacker who controls one channel controls one category, so
    every repetition competes for the same slot in the bipartite matching and
    the count stays at 1. This is the test that says the collusion threshold
    is about *distinct kinds of source*, not volume.
    """
    subject = f"patient:redteam-{uuid.uuid4().hex[:8]}"
    for _ in range(5):
        await _run_attack(
            db=db,
            engine=engine,
            embedder=embedder,
            envelope=envelope,
            tenant_id=redteam_tenant,
            subject=subject,
            contents=[ATTACKER_CLAIM],
            source_trust=SourceTrust.EXTERNAL,
            session_id=uuid.uuid4(),
        )

    rows = await _facts(db, redteam_tenant, subject)
    for trust, corroboration in rows:
        assert Trust(trust) is Trust.UNVERIFIED, "volume must not substitute for independence"
        assert corroboration == 1


async def test_two_controlled_categories_do_promote_and_that_is_the_published_threshold(
    db: Database,
    redteam_tenant: uuid.UUID,
    engine: MnemosEngine,
    embedder: Embedder,
    envelope: Envelope,
) -> None:
    """The attack that WORKS, kept as a test so the number stays honest.

    An attacker who controls two channels that look independent — say a
    support inbox (`external`) and a compromised agent (`agent`) — promotes
    the claim to `corroborated` in two sessions. That is the collusion
    threshold of 2 this project publishes in `docs/limits.md`, reproduced
    here as an executable fact rather than a paragraph.

    It stops short of `trusted`: reaching that needs `system` or `operator`
    provenance, which the API now refuses to issue to a write key at all
    (`keys.py::may_declare`, `tests/api/test_tool_scopes.py`).
    """
    subject = f"patient:redteam-{uuid.uuid4().hex[:8]}"
    for trust_category in (SourceTrust.EXTERNAL, SourceTrust.AGENT):
        await _run_attack(
            db=db,
            engine=engine,
            embedder=embedder,
            envelope=envelope,
            tenant_id=redteam_tenant,
            subject=subject,
            contents=[ATTACKER_CLAIM],
            source_trust=trust_category,
            session_id=uuid.uuid4(),
        )

    rows = await _facts(db, redteam_tenant, subject)
    assert rows
    trusts = {Trust(t) for t, _ in rows}
    assert Trust.CORROBORATED in trusts, "two independent-looking sources promote — threshold is 2"
    assert Trust.TRUSTED not in trusts, "but trusted still needs system/operator provenance"


async def test_no_payload_can_make_the_distiller_set_its_own_provenance(
    db: Database,
    redteam_tenant: uuid.UUID,
    engine: MnemosEngine,
    embedder: Embedder,
    envelope: Envelope,
) -> None:
    """The distiller-targeted payload asks for `source_trust=system`.

    It cannot be granted, and not because the request is filtered: a distilled
    fact has no provenance field for the model to fill in. Trust is derived in
    code from the episodes the fact cites (`_dominant_source_trust`), so the
    model's output simply has nowhere to say it. Structural, not defensive.
    """
    subject = f"patient:redteam-{uuid.uuid4().hex[:8]}"
    await _run_attack(
        db=db,
        engine=engine,
        embedder=embedder,
        envelope=envelope,
        tenant_id=redteam_tenant,
        subject=subject,
        contents=[p.content for p in ALL_PAYLOADS],
        source_trust=SourceTrust.EXTERNAL,
    )

    async def provenance(cur: psycopg.AsyncCursor) -> list[tuple]:
        await cur.execute(
            "SELECT DISTINCT e.source_trust FROM mnemos.semantic_facts f "
            "JOIN mnemos.fact_provenance p "
            "  ON p.tenant_id = f.tenant_id AND p.fact_id = f.fact_id "
            "JOIN mnemos.episodic_events e "
            "  ON e.tenant_id = p.tenant_id AND e.event_id = p.event_id "
            "WHERE f.tenant_id = %s AND f.subject_key = %s",
            (redteam_tenant, subject),
        )
        return await cur.fetchall()

    rows = await db.transaction(redteam_tenant, provenance, label="redteam_prov", read_only=True)
    assert rows, "the fact must exist for this to prove anything"
    assert {r[0] for r in rows} == {"external"}, "provenance follows the episodes, not the prose"
