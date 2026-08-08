"""revoke_source() — blast-radius revocation.

The operation nobody else can offer, because it needs vectors, provenance, and
the audit log to share one transaction boundary. Given a poisoned episode, this:

  1. computes the blast radius via `mnemos_engine.integrity.blast_radius`
     (transitive: facts -> dependent corroborations -> skills -> recalls ->
     actions -> laundered descendant episodes);
  2. in ONE serializable transaction, quarantines every derived fact and
     clears its vector/text-search entries, quarantines every cited skill
     version, marks every affected recall_log row `contaminated_at`, and
     marks every affected action `contaminated_at` + `contaminated_by`;
  3. appends the audit row carrying the full radius manifest;
  4. (Phase 02.7 wiring) the CHANGEFEED on `revocations` carries this to
     downstream consumers automatically — no separate publish step needed
     here.

Idempotent: revoking a source twice, or a source whose descendants were
already forgotten, must succeed cleanly rather than error.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

import psycopg
from mnemos_engine.integrity import BlastRadius, blast_radius
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Op
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


async def revoke_source(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    source_event_ids: list[UUID],
    *,
    actor: str,
    reason: str,
) -> RevocationRecord:
    """Quarantine everything a poisoned source touched, transitively, in one
    transaction. Facts and skills are QUARANTINED rather than deleted — a
    revocation is a claim that evidence should not be trusted, not a claim
    that the record of what happened should vanish. An operator who later
    confirms the poisoning can `forget` explicitly; revocation alone keeps
    the incident reconstructable."""
    radius = await blast_radius(cur, tenant_id, source_event_ids)
    revocation_id = uuid4()

    await append_audit(
        cur,
        tenant_id,
        op=Op.REVOKE,
        actor=actor,
        reason=reason,
        payload={"revocation_id": str(revocation_id), **radius.manifest()},
    )

    if radius.facts:
        fact_ids = [f.fact_id for f in radius.facts]
        await cur.execute(
            "UPDATE mnemos.semantic_facts SET trust = 'quarantined', revoked_at = now(), "
            "quarantine_reason = %s, embedding = NULL, tsv = NULL, updated_at = now() "
            "WHERE tenant_id = %s AND fact_id = ANY(%s)",
            (f"revoked: {reason}", tenant_id, fact_ids),
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
            Json(radius.manifest()),
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
        radius_manifest=radius.manifest(),
    )
