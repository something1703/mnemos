"""revoke_source() — blast-radius revocation.

The operation nobody else can offer, because it needs vectors, provenance, and
the audit log to share one transaction boundary. Given a poisoned episode, this:

  1. computes the blast radius via `mnemos_engine.integrity.blast_radius`
     (transitive: facts -> dependent corroborations -> skills -> recalls ->
     actions -> laundered descendant episodes);
  2. in ONE serializable transaction, for every touched fact: recomputes its
     corroboration with the tainted episodes' provenance edges set aside
     (`mnemos_engine.corroboration.independent_corroboration`). A fact that
     still has genuine independent support **survives**, demoted to whatever
     trust that remaining evidence actually earns; a fact with no support
     left once the tainted evidence is set aside is quarantined outright.
     Every quarantined fact and skill version has its vector/text-search
     entries cleared, every affected recall_log row is marked
     `contaminated_at`, and every affected action is marked
     `contaminated_at` + `contaminated_by`;
  3. appends the audit row carrying the full radius manifest plus the
     demoted/quarantined decision;
  4. (Phase 02.7 wiring) the CHANGEFEED on `revocations` carries this to
     downstream consumers automatically — no separate publish step needed
     here.

Over-revocation is as much a bug as under-revocation (PHASE_06 6.5): a fact
corroborated by a revoked source AND two genuinely independent sources should
not be destroyed just because it also touched the poisoned episode — that is
what step 2's per-fact recomputation is for, and it is why `revoke_source`
does not simply quarantine every fact `blast_radius` returns.

Idempotent: revoking a source twice, or a source whose descendants were
already forgotten, must succeed cleanly rather than error.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

import psycopg
from mnemos_engine.corroboration import determine_trust, independent_corroboration
from mnemos_engine.integrity import BlastRadius, ContaminatedFact, blast_radius
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Op, Trust
from psycopg.types.json import Json

from .models import RevocationRecord

log = logging.getLogger("mnemos.warden.revoke")


async def preview_revocation(
    cur: psycopg.AsyncCursor, tenant_id: UUID, source_event_ids: list[UUID]
) -> BlastRadius:
    """What revoking these sources would touch, before anything is touched.

    Identical call to what `revoke_source` uses internally — there is no
    second, hopefully-consistent preview query."""
    return await blast_radius(cur, tenant_id, source_event_ids)


async def _decide_fact_fates(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    radius: BlastRadius,
    tainted_event_ids: set[UUID],
) -> tuple[list[ContaminatedFact], list[tuple[ContaminatedFact, int, Trust]]]:
    """For every fact the blast radius touched, decide whether it survives
    with its corroboration demoted or has no support left once the tainted
    provenance is set aside.

    Facts already `quarantined` (a re-revocation, or a fact an earlier
    incident already reached) are skipped entirely — nothing to decide, and
    re-deciding them risks the opposite bug: a quarantined fact whose
    remaining signatures happen to count to two must not be silently promoted
    back by this path (`determine_trust`'s own CONTESTED/QUARANTINED
    passthrough guards this, but skipping avoids even querying for it).
    """
    if not radius.facts:
        return [], []

    fact_ids = [f.fact_id for f in radius.facts]
    await cur.execute(
        "SELECT fact_id, corroboration_count FROM mnemos.semantic_facts "
        "WHERE tenant_id = %s AND fact_id = ANY(%s)",
        (tenant_id, fact_ids),
    )
    previous_counts = {r[0]: int(r[1]) for r in await cur.fetchall()}

    quarantined: list[ContaminatedFact] = []
    demoted: list[tuple[ContaminatedFact, int, Trust]] = []

    for fact in radius.facts:
        current_trust = Trust(fact.trust)
        if current_trust is Trust.QUARANTINED:
            continue

        count, has_trusted_source = await independent_corroboration(
            cur, tenant_id, fact.fact_id, exclude_event_ids=tainted_event_ids
        )
        new_trust = determine_trust(
            current_trust, corroboration_count=count, has_trusted_source=has_trusted_source
        )

        if new_trust in (Trust.CORROBORATED, Trust.TRUSTED):
            if new_trust != current_trust or count != previous_counts.get(fact.fact_id):
                demoted.append((fact, count, new_trust))
        else:
            quarantined.append(fact)

    return quarantined, demoted


async def revoke_source(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    source_event_ids: list[UUID],
    *,
    actor: str,
    reason: str,
) -> RevocationRecord:
    """Quarantine everything a poisoned source touched with no surviving
    support, in one transaction. Facts are QUARANTINED rather than deleted —
    a revocation is a claim that evidence should not be trusted, not a claim
    that the record of what happened should vanish. An operator who later
    confirms the poisoning can `forget` explicitly; revocation alone keeps
    the incident reconstructable.

    A fact touched by the blast radius but still independently corroborated
    by evidence outside it is not destroyed — see this module's docstring
    and `_decide_fact_fates`."""
    radius = await blast_radius(cur, tenant_id, source_event_ids)
    revocation_id = uuid4()
    tainted_event_ids = set(source_event_ids) | set(radius.derived_event_ids)

    quarantined_facts, demoted_facts = await _decide_fact_fates(
        cur, tenant_id, radius, tainted_event_ids
    )

    decision = {
        "quarantined_fact_ids": [str(f.fact_id) for f in quarantined_facts],
        "demoted_fact_ids": [str(f.fact_id) for f, _c, _t in demoted_facts],
    }
    await append_audit(
        cur,
        tenant_id,
        op=Op.REVOKE,
        actor=actor,
        reason=reason,
        payload={"revocation_id": str(revocation_id), **radius.manifest(), **decision},
    )

    if quarantined_facts:
        fact_ids = [f.fact_id for f in quarantined_facts]
        await cur.execute(
            "UPDATE mnemos.semantic_facts SET trust = 'quarantined', revoked_at = now(), "
            "quarantine_reason = %s, embedding = NULL, tsv = NULL, updated_at = now() "
            "WHERE tenant_id = %s AND fact_id = ANY(%s)",
            (f"revoked: {reason}", tenant_id, fact_ids),
        )

    for fact, count, new_trust in demoted_facts:
        await append_audit(
            cur,
            tenant_id,
            op=Op.DEMOTE,
            actor=actor,
            subject_key=fact.subject_key,
            reason=f"corroboration recomputed after revoking source(s) for: {reason}",
            payload={
                "fact_id": str(fact.fact_id),
                "revocation_id": str(revocation_id),
                "from": str(Trust(fact.trust)),
                "to": str(new_trust),
                "corroboration_count": count,
            },
        )
        await cur.execute(
            "UPDATE mnemos.semantic_facts SET trust = %s, corroboration_count = %s, "
            "updated_at = now() WHERE tenant_id = %s AND fact_id = %s",
            (str(new_trust), count, tenant_id, fact.fact_id),
        )

    if radius.skills:
        for skill in radius.skills:
            await cur.execute(
                "UPDATE mnemos.skill_versions SET trust = 'quarantined', "
                "quarantined_at = now() "
                "WHERE tenant_id = %s AND skill_id = %s AND version = %s",
                (tenant_id, skill.skill_id, skill.version),
            )

    if radius.recall_ids:
        await cur.execute(
            "UPDATE mnemos.recall_log SET contaminated_at = now() "
            "WHERE tenant_id = %s AND recall_id = ANY(%s)",
            (tenant_id, radius.recall_ids),
        )

    if radius.action_ids:
        await cur.execute(
            "UPDATE mnemos.action_log SET contaminated_at = now(), contaminated_by = %s "
            "WHERE tenant_id = %s AND action_id = ANY(%s)",
            (revocation_id, tenant_id, radius.action_ids),
        )

    manifest = {**radius.manifest(), **decision}
    await cur.execute(
        """
        INSERT INTO mnemos.revocations
            (tenant_id, revocation_id, source_event_id, reason, actor, radius_manifest)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING created_at
        """,
        (
            tenant_id,
            revocation_id,
            source_event_ids[0] if len(source_event_ids) == 1 else None,
            reason,
            actor,
            Json(manifest),
        ),
    )
    row = await cur.fetchone()
    assert row is not None

    log.warning(
        "source revoked",
        extra={
            "tenant_id": str(tenant_id),
            "revocation_id": str(revocation_id),
            "summary": radius.summary(),
        },
    )

    return RevocationRecord(
        tenant_id=tenant_id,
        revocation_id=revocation_id,
        source_event_ids=source_event_ids,
        reason=reason,
        actor=actor,
        created_at=row[0],
        radius_manifest=manifest,
    )
