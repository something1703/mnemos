"""`mnemos_warden.approvals.enforce` — the dual-control handshake in
isolation from any particular gated operation.

`mnemos.tenants.dual_control` (migration 001) and `DualControlRequired`
(`errors.py`) both predate this file and neither was ever read or raised
anywhere — this is the module that actually makes the tenant flag mean
something, tested directly against a real local cluster rather than only
through `Warden.forget`/`revoke_source`/`set_legal_hold`, which
`tests/api/test_dual_control.py` covers end to end through real MCP dispatch.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from mnemos_warden.approvals import enforce
from mnemos_warden.errors import DualControlRequired

pytestmark = pytest.mark.invariant


async def _enable_dual_control(db, tenant_id: uuid.UUID) -> None:
    async def run(cur):
        await cur.execute(
            "UPDATE mnemos.tenants SET dual_control = true WHERE tenant_id = %s", (tenant_id,)
        )

    await db.transaction(tenant_id, run, label="enable_dual_control")


async def test_off_by_default_does_not_block_a_single_admin(db, tenant) -> None:
    # Must not raise — the tenant's dual_control flag defaults false.
    await enforce(
        db,
        tenant,
        operation="forget",
        target_key="patient:x",
        reason="test",
        admin_key_id=uuid.uuid4(),
        admin_label="solo-admin",
    )


async def test_first_approval_is_refused_and_recorded(db, tenant) -> None:
    await _enable_dual_control(db, tenant)

    with pytest.raises(DualControlRequired) as exc_info:
        await enforce(
            db,
            tenant,
            operation="forget",
            target_key="patient:x",
            reason="confirmed poisoning",
            admin_key_id=uuid.uuid4(),
            admin_label="first-admin",
        )
    assert exc_info.value.first_approver == "first-admin"

    async def count(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.pending_approvals "
            "WHERE tenant_id = %s AND operation = 'forget' AND target_key = 'patient:x'",
            (tenant,),
        )
        return (await cur.fetchone())[0]

    assert await db.transaction(tenant, count, label="verify") == 1


async def test_same_key_calling_twice_never_satisfies_dual_control(db, tenant) -> None:
    await _enable_dual_control(db, tenant)
    key_id = uuid.uuid4()

    for _ in range(2):
        with pytest.raises(DualControlRequired) as exc_info:
            await enforce(
                db,
                tenant,
                operation="forget",
                target_key="patient:x",
                reason="test",
                admin_key_id=key_id,
                admin_label="lone-admin",
            )
        assert exc_info.value.first_approver == "lone-admin"


async def test_second_distinct_admin_satisfies_and_consumes_the_pending_approval(
    db, tenant
) -> None:
    await _enable_dual_control(db, tenant)

    with pytest.raises(DualControlRequired):
        await enforce(
            db,
            tenant,
            operation="forget",
            target_key="patient:x",
            reason="test",
            admin_key_id=uuid.uuid4(),
            admin_label="first-admin",
        )

    # Must not raise.
    await enforce(
        db,
        tenant,
        operation="forget",
        target_key="patient:x",
        reason="test",
        admin_key_id=uuid.uuid4(),
        admin_label="second-admin",
    )

    async def count(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.pending_approvals "
            "WHERE tenant_id = %s AND operation = 'forget' AND target_key = 'patient:x'",
            (tenant,),
        )
        return (await cur.fetchone())[0]

    assert await db.transaction(tenant, count, label="verify") == 0, (
        "the pending approval must be consumed, not left for a third caller to reuse"
    )


async def test_a_different_target_does_not_share_an_approval(db, tenant) -> None:
    """Approving a forget of subject A must never let a forget of subject B
    through — the whole point of scoping by target_key, not just operation."""
    await _enable_dual_control(db, tenant)

    with pytest.raises(DualControlRequired):
        await enforce(
            db,
            tenant,
            operation="forget",
            target_key="patient:a",
            reason="test",
            admin_key_id=uuid.uuid4(),
            admin_label="first-admin",
        )

    with pytest.raises(DualControlRequired):
        await enforce(
            db,
            tenant,
            operation="forget",
            target_key="patient:b",
            reason="test",
            admin_key_id=uuid.uuid4(),
            admin_label="second-admin",
        )


async def test_a_different_operation_does_not_share_an_approval(db, tenant) -> None:
    """A shred approval must never satisfy a forget on the same subject —
    they are different-magnitude operations even against the same target."""
    await _enable_dual_control(db, tenant)

    with pytest.raises(DualControlRequired):
        await enforce(
            db,
            tenant,
            operation="shred",
            target_key="patient:x",
            reason="test",
            admin_key_id=uuid.uuid4(),
            admin_label="first-admin",
        )

    with pytest.raises(DualControlRequired) as exc_info:
        await enforce(
            db,
            tenant,
            operation="forget",
            target_key="patient:x",
            reason="test",
            admin_key_id=uuid.uuid4(),
            admin_label="second-admin",
        )
    assert exc_info.value.first_approver == "second-admin"


async def test_an_expired_pending_approval_is_treated_as_gone(db, tenant) -> None:
    await _enable_dual_control(db, tenant)

    with pytest.raises(DualControlRequired):
        await enforce(
            db,
            tenant,
            operation="forget",
            target_key="patient:x",
            reason="test",
            admin_key_id=uuid.uuid4(),
            admin_label="first-admin",
            ttl=timedelta(seconds=-1),
        )

    with pytest.raises(DualControlRequired) as exc_info:
        await enforce(
            db,
            tenant,
            operation="forget",
            target_key="patient:x",
            reason="test",
            admin_key_id=uuid.uuid4(),
            admin_label="second-admin",
        )
    # Not satisfied — the expired approval does not count, so this call is
    # itself treated as a fresh first approval, not a second one satisfying a
    # dead one.
    assert exc_info.value.first_approver == "second-admin"
