"""Weekly decay: unrecalled facts weaken over time, and unverified facts that
never earned corroboration stop being ambient noise.

Two independent sweeps, both never-delete:

  * `decay_tenant_facts` — exponential strength decay for facts nobody has
    recalled recently, floored at 0.1 (the schema's own CHECK constraint,
    so this can never drift below what the database already promises).
  * `quarantine_stale_unverified` (in `corroboration.py`) — unverified facts
    that missed their corroboration window move to `quarantined`.

Episodic-tier decay is not here. It is Row-Level TTL, native to CockroachDB
(migration 002's `ttl_expiration_expression`), and it already respects legal
holds because the Warden sets `expire_at = NULL` on any subject under one —
there is nothing for this service to do for that tier, and adding a redundant
code path here would be a second place that guarantee could drift from the
first.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Op

log = logging.getLogger("mnemos.sleep_cycle.decay")

DECAY_LAMBDA_DEFAULT = 0.1
"""Per-week decay rate. At this rate a fact idle for a year (52 weeks) decays
to roughly 0.5% of its starting strength before hitting the floor — the point
is that lingering, unused memory should stop dominating retrieval, not that
this specific half-life is load-bearing; tune via Settings without touching
the formula."""

MIN_STRENGTH = 0.1
"""Matches `ck_facts_strength CHECK (strength >= 0.1)` in migration 003.
Decay approaches but never breaches the schema's own floor."""

IDLE_THRESHOLD_DAYS = 14
"""Facts recalled within the last two weeks are exempt — decay only applies
to what has genuinely gone unused."""


def decayed_strength(
    strength: float, weeks_idle: float, *, lam: float = DECAY_LAMBDA_DEFAULT
) -> float:
    """Pure function, independent of the database, so the curve can be
    asserted against with frozen time rather than by waiting real weeks or
    faking clock state inside a transaction."""
    if weeks_idle <= 0:
        return strength
    return max(MIN_STRENGTH, strength * math.exp(-lam * weeks_idle))


async def decay_tenant_facts(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    actor: str,
    lam: float = DECAY_LAMBDA_DEFAULT,
    now: datetime | None = None,
) -> int:
    """Decay every eligible fact's strength in one tenant. Returns the count
    actually changed (a fact already at the floor is skipped, not re-audited
    for a no-op).

    One audit row covers the whole sweep — `_reinforce`'s bulk UPDATE pattern
    in `mnemos_engine.engine` does the same for the same reason: invariant 2
    requires an audit row per transaction that changes state, not one per row
    changed, and a decay sweep touching thousands of facts should not write
    thousands of near-identical audit entries to say so.
    """
    now = now or datetime.now(UTC)
    threshold = now - timedelta(days=IDLE_THRESHOLD_DAYS)

    await cur.execute(
        "SELECT fact_id, strength, COALESCE(last_recalled_at, created_at) "
        "FROM mnemos.semantic_facts "
        "WHERE tenant_id = %s AND superseded_by IS NULL AND revoked_at IS NULL "
        "AND COALESCE(last_recalled_at, created_at) < %s AND strength > %s",
        (tenant_id, threshold, MIN_STRENGTH),
    )
    rows = await cur.fetchall()
    if not rows:
        return 0

    updates: list[tuple[UUID, float]] = []
    for fact_id, strength, since in rows:
        weeks_idle = (now - since).total_seconds() / (7 * 86400)
        new_strength = decayed_strength(float(strength), weeks_idle, lam=lam)
        if new_strength < float(strength):
            updates.append((fact_id, new_strength))

    if not updates:
        return 0

    await append_audit(
        cur,
        tenant_id,
        op=Op.DECAY,
        actor=actor,
        subject_key=None,
        payload={
            "fact_count": len(updates),
            "lambda": str(lam),
            "idle_threshold_days": str(IDLE_THRESHOLD_DAYS),
        },
    )
    for fact_id, new_strength in updates:
        await cur.execute(
            "UPDATE mnemos.semantic_facts SET strength = %s, updated_at = now() "
            "WHERE tenant_id = %s AND fact_id = %s",
            (new_strength, tenant_id, fact_id),
        )
    log.info("decayed facts", extra={"tenant_id": str(tenant_id), "count": len(updates)})
    return len(updates)


__all__ = [
    "DECAY_LAMBDA_DEFAULT",
    "IDLE_THRESHOLD_DAYS",
    "MIN_STRENGTH",
    "decay_tenant_facts",
    "decayed_strength",
]
