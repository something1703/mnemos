"""Dual control — a tenant-configurable two-distinct-admin-key rule for
destructive Warden operations.

`mnemos.tenants.dual_control` (migration 001) and `DualControlRequired`
(`errors.py`) both existed since early in this project's build, and neither
was ever read or raised anywhere — PHASE_06_GOVERNANCE_WARDEN.md's sub-phase
6.1 checklist and `docs/threat-model.md`'s erasure-abuse section both
describe this control as if it were live; this module is what actually makes
it live.

**Why this lives in `packages/warden`, not `services/api`.** An earlier
version of this lived at the API layer, matching `Warden`'s own now-corrected
docstring ("dual control is enforced at the API layer, not here"). It moved
here because consuming a pending approval DELETEs its row, and
`make no-delete-in-engine` enforces invariant 1 by grepping for the literal
text `DELETE FROM` anywhere outside `packages/warden` — a textual proof, not
a runtime one, and a raw DELETE statement in `services/api` would fail that
proof regardless of which database role actually executes it. Given that,
the state this control owns (who has approved what, and whether a second,
distinct admin still needs to) is exactly the kind of governance bookkeeping
the rest of this package already owns (legal holds, revocations, residency
policies) — so it belongs here on the merits too, not only because of the
grep.

**The handshake.** The first admin's call to a gated operation records a
pending approval and refuses to execute. A SECOND, DISTINCT admin key
calling the identical operation against the identical target consumes that
pending row and is let through. Pending approvals expire
(`DEFAULT_APPROVAL_TTL`) so a stale one-sided approval from hours ago cannot
be silently completed by an unrelated later admin call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
from mnemos_engine.db import Database

from .errors import DualControlRequired

log = logging.getLogger("mnemos.warden.approvals")

DEFAULT_APPROVAL_TTL = timedelta(minutes=15)
"""Long enough for a second admin to actually review and approve; short
enough that an approval nobody acted on stops being usable rather than
lingering as a standing, half-completed authorization."""


@dataclass(frozen=True)
class _Outcome:
    satisfied: bool
    first_approver: str | None = None


async def _tenant_requires_dual_control(cur: psycopg.AsyncCursor, tenant_id: UUID) -> bool:
    await cur.execute("SELECT dual_control FROM mnemos.tenants WHERE tenant_id = %s", (tenant_id,))
    row = await cur.fetchone()
    return bool(row and row[0])


async def _check_and_record(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    operation: str,
    target_key: str,
    reason: str,
    admin_key_id: UUID,
    admin_label: str,
    ttl: timedelta,
    now: datetime,
) -> _Outcome:
    """Never raises — every branch commits cleanly, including the ones that
    end with the caller being refused. The refusal itself is `enforce`'s job,
    once this has returned and its writes have landed, so a first approval's
    pending row survives even though that same call is still reported to its
    own caller as failed.
    """
    if not await _tenant_requires_dual_control(cur, tenant_id):
        return _Outcome(satisfied=True)

    await cur.execute(
        "SELECT approval_id, first_approver_key_id, first_approver_label "
        "FROM mnemos.pending_approvals "
        "WHERE tenant_id = %s AND operation = %s AND target_key = %s AND expires_at > %s "
        "ORDER BY requested_at DESC LIMIT 1",
        (tenant_id, operation, target_key, now),
    )
    pending = await cur.fetchone()

    if pending is not None:
        approval_id, first_key_id, first_label = pending
        if UUID(str(first_key_id)) != admin_key_id:
            await cur.execute(
                "DELETE FROM mnemos.pending_approvals WHERE tenant_id = %s AND approval_id = %s",
                (tenant_id, approval_id),
            )
            log.warning(
                "dual control satisfied",
                extra={
                    "tenant_id": str(tenant_id),
                    "operation": operation,
                    "target_key": target_key,
                    "first_approver": first_label,
                    "second_approver": admin_label,
                },
            )
            return _Outcome(satisfied=True)

        # The SAME key calling again is not a second, distinct approval — the
        # original pending row (and its original expiry) is left untouched
        # rather than refreshed, so one admin cannot extend their own pending
        # approval indefinitely by re-calling.
        return _Outcome(satisfied=False, first_approver=str(first_label))

    await cur.execute(
        "INSERT INTO mnemos.pending_approvals "
        "(tenant_id, operation, target_key, reason, first_approver_key_id, "
        " first_approver_label, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (tenant_id, operation, target_key, reason, admin_key_id, admin_label, now + ttl),
    )
    log.warning(
        "dual control: first approval recorded, awaiting a second distinct admin",
        extra={
            "tenant_id": str(tenant_id),
            "operation": operation,
            "target_key": target_key,
            "first_approver": admin_label,
        },
    )
    return _Outcome(satisfied=False, first_approver=admin_label)


async def enforce(
    db: Database,
    tenant_id: UUID,
    *,
    operation: str,
    target_key: str,
    reason: str,
    admin_key_id: UUID,
    admin_label: str,
    ttl: timedelta = DEFAULT_APPROVAL_TTL,
) -> None:
    """Returns normally when the caller may proceed: dual control is off for
    this tenant, or this call is a second, distinct admin's approval of an
    already-pending one. Raises `DualControlRequired` — after any
    pending-approval bookkeeping has already committed — when this is a
    first approval or a repeat from the same key.
    """
    now = datetime.now(UTC)

    async def run(cur: psycopg.AsyncCursor) -> _Outcome:
        return await _check_and_record(
            cur,
            tenant_id,
            operation=operation,
            target_key=target_key,
            reason=reason,
            admin_key_id=admin_key_id,
            admin_label=admin_label,
            ttl=ttl,
            now=now,
        )

    outcome = await db.transaction(tenant_id, run, label="dual_control")
    if not outcome.satisfied:
        assert outcome.first_approver is not None
        raise DualControlRequired(operation, outcome.first_approver)


__all__ = ["DEFAULT_APPROVAL_TTL", "enforce"]
