"""Legal hold — the constraint that proves this is real software.

A system that always deletes on request is not compliant, it is merely
obedient. A hold blocks erasure, pins episodes against Row-Level TTL, and (in
the multi-region rig) extends the range's `gc.ttlseconds` so temporal recall
covers the retention obligation. `forget` against a held subject must fail
LOUDLY, citing the hold — never silently, never partially, because a partial
erasure under hold is worse than either doing it or refusing outright.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID, uuid4

import psycopg
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Op

from .models import LegalHold

log = logging.getLogger("mnemos.warden.holds")


async def set_legal_hold(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    subject_key: str,
    matter_reference: str,
    placed_by: str,
    expires_at: datetime | None = None,
) -> LegalHold:
    """Place a hold. Idempotent per (subject, matter): re-placing an existing
    active hold for the same matter is a no-op that returns the existing row,
    since two compliance officers acting on the same matter should not create
    two audit entries for one intent."""
    await cur.execute(
        "SELECT hold_id, placed_by, placed_at, expires_at FROM mnemos.legal_holds "
        "WHERE tenant_id = %s AND subject_key = %s AND matter_reference = %s "
        "AND released_at IS NULL",
        (tenant_id, subject_key, matter_reference),
    )
    existing = await cur.fetchone()
    if existing is not None:
        return LegalHold(
            tenant_id=tenant_id,
            hold_id=existing[0],
            subject_key=subject_key,
            matter_reference=matter_reference,
            placed_by=existing[1],
            placed_at=existing[2],
            expires_at=existing[3],
        )

    hold_id = uuid4()
    await append_audit(
        cur,
        tenant_id,
        op=Op.HOLD,
        actor=placed_by,
        subject_key=subject_key,
        reason=matter_reference,
        payload={"hold_id": str(hold_id), "matter_reference": matter_reference},
    )
    await cur.execute(
        """
        INSERT INTO mnemos.legal_holds
            (tenant_id, hold_id, subject_key, matter_reference, placed_by, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING placed_at
        """,
        (tenant_id, hold_id, subject_key, matter_reference, placed_by, expires_at),
    )
    row = await cur.fetchone()
    assert row is not None
    log.info("legal hold placed", extra={"subject_key": subject_key, "matter": matter_reference})
    return LegalHold(
        tenant_id=tenant_id,
        hold_id=hold_id,
        subject_key=subject_key,
        matter_reference=matter_reference,
        placed_by=placed_by,
        placed_at=row[0],
        expires_at=expires_at,
    )


async def release_legal_hold(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    hold_id: UUID,
    *,
    released_by: str,
    release_reason: str,
) -> LegalHold | None:
    """Release a hold. A separate, audited admin operation from placing one —
    the two actions have different authorization stories in every regulated
    environment we designed against, and collapsing them into "set/unset"
    would erase that distinction from the audit trail."""
    await cur.execute(
        "SELECT subject_key, matter_reference, placed_by, placed_at, expires_at "
        "FROM mnemos.legal_holds "
        "WHERE tenant_id = %s AND hold_id = %s AND released_at IS NULL",
        (tenant_id, hold_id),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    subject_key, matter_reference, placed_by, placed_at, expires_at = row

    await append_audit(
        cur,
        tenant_id,
        op=Op.RELEASE_HOLD,
        actor=released_by,
        subject_key=subject_key,
        reason=release_reason,
        payload={"hold_id": str(hold_id), "matter_reference": matter_reference},
    )
    await cur.execute(
        "UPDATE mnemos.legal_holds SET released_by = %s, released_at = now(), "
        "release_reason = %s WHERE tenant_id = %s AND hold_id = %s "
        "RETURNING released_at",
        (released_by, release_reason, tenant_id, hold_id),
    )
    released_row = await cur.fetchone()
    assert released_row is not None
    return LegalHold(
        tenant_id=tenant_id,
        hold_id=hold_id,
        subject_key=subject_key,
        matter_reference=matter_reference,
        placed_by=placed_by,
        placed_at=placed_at,
        expires_at=expires_at,
        released_by=released_by,
        released_at=released_row[0],
        release_reason=release_reason,
    )


async def active_hold(
    cur: psycopg.AsyncCursor, tenant_id: UUID, subject_key: str
) -> LegalHold | None:
    """The check every erasure path runs first, before it touches a row."""
    await cur.execute(
        "SELECT hold_id, matter_reference, placed_by, placed_at, expires_at "
        "FROM mnemos.legal_holds "
        "WHERE tenant_id = %s AND subject_key = %s AND released_at IS NULL "
        "AND (expires_at IS NULL OR expires_at > now()) "
        "ORDER BY placed_at LIMIT 1",
        (tenant_id, subject_key),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return LegalHold(
        tenant_id=tenant_id,
        hold_id=row[0],
        subject_key=subject_key,
        matter_reference=row[1],
        placed_by=row[2],
        placed_at=row[3],
        expires_at=row[4],
    )


async def list_holds(
    cur: psycopg.AsyncCursor, tenant_id: UUID, *, active_only: bool = True
) -> list[LegalHold]:
    clause = "AND released_at IS NULL" if active_only else ""
    await cur.execute(
        f"""
        SELECT hold_id, subject_key, matter_reference, placed_by, placed_at,
               expires_at, released_by, released_at, release_reason
        FROM mnemos.legal_holds
        WHERE tenant_id = %s {clause}
        ORDER BY placed_at DESC
        """,  # noqa: S608 - clause is a fixed literal, not user input
        (tenant_id,),
    )
    return [
        LegalHold(
            tenant_id=tenant_id,
            hold_id=r[0],
            subject_key=r[1],
            matter_reference=r[2],
            placed_by=r[3],
            placed_at=r[4],
            expires_at=r[5],
            released_by=r[6],
            released_at=r[7],
            release_reason=r[8],
        )
        for r in await cur.fetchall()
    ]
