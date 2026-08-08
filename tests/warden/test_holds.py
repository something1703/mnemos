"""Legal hold — placement, idempotency, release, and the audit split between
the two, direct against packages/warden/holds.py (below the Warden's
confirm/reason gate, which is tested separately in test_confirm_gate.py)."""

from __future__ import annotations

import uuid

import pytest
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Op
from mnemos_warden.holds import active_hold, list_holds, release_legal_hold, set_legal_hold

pytestmark = pytest.mark.invariant


async def _place(db, tenant, subject, matter, placed_by="compliance:officer"):
    async def run(cur):
        return await set_legal_hold(
            cur, tenant, subject_key=subject, matter_reference=matter, placed_by=placed_by
        )

    return await db.transaction(tenant, run, label="set_hold")


async def test_placing_a_hold_is_visible_to_active_hold(db, tenant: uuid.UUID) -> None:
    await _place(db, tenant, "patient:1", "MATTER-1")

    async def check(cur):
        return await active_hold(cur, tenant, "patient:1")

    hold = await db.transaction(tenant, check, label="check", read_only=True)
    assert hold is not None
    assert hold.matter_reference == "MATTER-1"
    assert hold.is_active


async def test_no_hold_returns_none(db, tenant: uuid.UUID) -> None:
    async def check(cur):
        return await active_hold(cur, tenant, "patient:no-hold")

    assert await db.transaction(tenant, check, label="check", read_only=True) is None


async def test_placing_the_same_hold_twice_is_idempotent(db, tenant: uuid.UUID) -> None:
    """Two compliance officers acting on the same matter should not create
    two audit entries for one intent."""
    first = await _place(db, tenant, "patient:idem", "MATTER-IDEM")
    second = await _place(db, tenant, "patient:idem", "MATTER-IDEM")
    assert first.hold_id == second.hold_id

    async def count_audit(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.audit_chain WHERE tenant_id = %s AND op = 'hold' "
            "AND subject_key = %s",
            (tenant, "patient:idem"),
        )
        return (await cur.fetchone())[0]

    assert await db.transaction(tenant, count_audit, label="count") == 1


async def test_different_matters_on_the_same_subject_are_distinct_holds(
    db, tenant: uuid.UUID
) -> None:
    """Idempotency is per (subject, matter) — a second, genuinely different
    matter must not be silently merged into the first."""
    first = await _place(db, tenant, "patient:multi", "MATTER-A")
    second = await _place(db, tenant, "patient:multi", "MATTER-B")
    assert first.hold_id != second.hold_id

    async def check(cur):
        return await list_holds(cur, tenant)

    holds = await db.transaction(tenant, check, label="list", read_only=True)
    matters = {h.matter_reference for h in holds if h.subject_key == "patient:multi"}
    assert matters == {"MATTER-A", "MATTER-B"}


async def test_release_is_a_separate_audited_operation(db, tenant: uuid.UUID) -> None:
    """Placing and releasing have different authorization stories in every
    regulated environment this was designed against, so they must not
    collapse into one 'set/unset' audit entry."""
    hold = await _place(db, tenant, "patient:release", "MATTER-R")

    async def release(cur):
        return await release_legal_hold(
            cur, tenant, hold.hold_id, released_by="compliance:lead", release_reason="closed"
        )

    released = await db.transaction(tenant, release, label="release")
    assert released is not None
    assert released.released_by == "compliance:lead"
    assert not released.is_active

    async def check(cur):
        return await active_hold(cur, tenant, "patient:release")

    assert await db.transaction(tenant, check, label="check", read_only=True) is None

    async def ops(cur):
        await cur.execute(
            "SELECT op FROM mnemos.audit_chain WHERE tenant_id = %s AND subject_key = %s "
            "ORDER BY committed_at",
            (tenant, "patient:release"),
        )
        return [r[0] for r in await cur.fetchall()]

    assert await db.transaction(tenant, ops, label="ops") == ["hold", "release_hold"]


async def test_releasing_an_already_released_hold_is_a_no_op(db, tenant: uuid.UUID) -> None:
    hold = await _place(db, tenant, "patient:double-release", "MATTER-DR")

    async def release(cur):
        return await release_legal_hold(
            cur, tenant, hold.hold_id, released_by="c", release_reason="first"
        )

    await db.transaction(tenant, release, label="release")
    result = await db.transaction(tenant, release, label="release_again")
    assert result is None, "an already-released hold has nothing left to release"


async def test_expired_hold_does_not_block(db, tenant: uuid.UUID) -> None:
    """expires_at in the past means the hold no longer governs — checked at
    the SQL level (`expires_at > now()`), not by a background sweep that
    could lag."""
    from datetime import UTC, datetime, timedelta

    async def place_expired(cur):
        await append_audit(cur, tenant, op=Op.HOLD, actor="test", subject_key="patient:expired")
        await cur.execute(
            "INSERT INTO mnemos.legal_holds "
            "(tenant_id, subject_key, matter_reference, placed_by, expires_at) "
            "VALUES (%s, %s, 'MATTER-EXP', 'c', %s)",
            (tenant, "patient:expired", datetime.now(UTC) - timedelta(days=1)),
        )

    await db.transaction(tenant, place_expired, label="place")

    async def check(cur):
        return await active_hold(cur, tenant, "patient:expired")

    assert await db.transaction(tenant, check, label="check", read_only=True) is None


async def test_list_holds_active_only_excludes_released(db, tenant: uuid.UUID) -> None:
    active = await _place(db, tenant, "patient:list-a", "M-A")
    released = await _place(db, tenant, "patient:list-b", "M-B")

    async def release(cur):
        await release_legal_hold(
            cur, tenant, released.hold_id, released_by="c", release_reason="done"
        )

    await db.transaction(tenant, release, label="release")

    async def list_active(cur):
        return await list_holds(cur, tenant, active_only=True)

    holds = await db.transaction(tenant, list_active, label="list", read_only=True)
    ids = {h.hold_id for h in holds}
    assert active.hold_id in ids
    assert released.hold_id not in ids
