"""The corroboration gate — the single mechanism that makes memory poisoning
hard, and the reason this module gets more scrutiny than any other file in the
sleep cycle.

**The definition "independent" carries all the weight.** Two pieces of
evidence corroborate a fact only if they come from a different session AND a
different source-trust origin. Getting this wrong in either direction breaks
the product:

  * Too loose (e.g. "two facts" is enough, regardless of origin) and a single
    poisoned document that gets distilled into five mutually-supporting facts
    can promote itself to `trusted` — the exact attack Phase 10 red-teams.
  * Too strict (e.g. requiring a different subject too) and legitimate
    memory can never earn trust, because most corroboration in practice comes
    from the same subject being discussed again in a later, unrelated
    session.

`corroboration_count` is never incremented — it is recomputed from the ground
truth on every write: the number of *distinct* `(session_id, source_trust)`
pairs among the episodes a fact's provenance edges point at. An incremented
counter can drift from what the provenance graph actually shows; a recomputed
one cannot, by construction. That is worth the extra query.

Promotion and demotion are audited state transitions with the evidence that
justified them (`op='promote'`, `op='quarantine'`), matching the requirement
that a trust change be as explainable as the fact itself.

The actual counting algorithm (`max_independent_corroborations`) and the
promotion rule (`determine_trust`) now live in `mnemos_engine.corroboration`,
re-exported here, because `mnemos_warden.revoke` needs the identical
arithmetic to demote a fact's corroboration when one of its sources turns out
to be poisoned — see that module's docstring for why a second copy would be
dangerous.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg
from mnemos_engine.corroboration import determine_trust as determine_trust
from mnemos_engine.corroboration import independent_corroboration as _independent_corroboration
from mnemos_engine.corroboration import (
    max_independent_corroborations as _max_independent_corroborations,
)
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Op, Trust

log = logging.getLogger("mnemos.sleep_cycle.corroboration")

DEFAULT_TTL_DAYS = 30
"""How long an unverified fact may wait for corroboration before it is
quarantined. Long enough that a second, independent session naturally arrives
for most legitimate subjects; short enough that stale unverified claims do not
accumulate as ambient noise an agent might stumble into via
`include_unverified=True`."""


async def recompute_corroboration(
    cur: psycopg.AsyncCursor, tenant_id: UUID, fact_id: UUID
) -> tuple[int, bool]:
    """Recount independent corroborating sources from the provenance graph.

    Returns `(corroboration_count, has_trusted_source)`. Thin wrapper over
    `mnemos_engine.corroboration.independent_corroboration` kept here under
    its original name because callers in this package (and its tests) already
    know it by this name; the shared implementation is what actually runs.
    """
    return await _independent_corroboration(cur, tenant_id, fact_id)


async def apply_trust_transition(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    fact_id: UUID,
    subject_key: str,
    *,
    actor: str,
) -> tuple[Trust, Trust]:
    """Recompute corroboration, decide the resulting trust state, and commit
    the change if it moved. Returns `(previous, current)`.

    Safe to call after any provenance-edge-adding operation (a new fact's
    first edges, or a reinforcement's additional ones) — recomputing from
    scratch means calling it twice with no new evidence writes nothing at all,
    not a stale value re-saved under a fresh timestamp.

    Every write here is preceded by its own `append_audit` call, immediately
    before the specific statement it covers — not one ticket reused across
    two UPDATEs. A ticket only satisfies migration 010's trigger for the
    statement that runs while it is the current `SET LOCAL` value, and this
    function found that out the hard way: an earlier version wrote
    `corroboration_count` unconditionally before checking whether trust also
    moved, with no ticket armed for that write at all when this was the first
    statement in a fresh transaction (exactly the shape `apply_trust_transition`
    is called in from `_insert_fact`). Caught by
    `test_apply_trust_transition_promotes_unverified_to_corroborated`, not by
    inspection.
    """
    await cur.execute(
        "SELECT trust, corroboration_count FROM mnemos.semantic_facts "
        "WHERE tenant_id = %s AND fact_id = %s",
        (tenant_id, fact_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise ValueError(f"fact {fact_id} not found for tenant {tenant_id}")
    previous = Trust(row[0])
    previous_count = int(row[1])

    count, has_trusted_source = await recompute_corroboration(cur, tenant_id, fact_id)
    current = determine_trust(
        previous, corroboration_count=count, has_trusted_source=has_trusted_source
    )

    if count != previous_count:
        await append_audit(
            cur,
            tenant_id,
            op=Op.REINFORCE,
            actor=actor,
            subject_key=subject_key,
            payload={
                "fact_id": str(fact_id),
                "corroboration_count_from": str(previous_count),
                "corroboration_count_to": str(count),
            },
        )
        await cur.execute(
            "UPDATE mnemos.semantic_facts SET corroboration_count = %s, updated_at = now() "
            "WHERE tenant_id = %s AND fact_id = %s",
            (count, tenant_id, fact_id),
        )

    if current == previous:
        return previous, current

    await append_audit(
        cur,
        tenant_id,
        op=Op.PROMOTE,
        actor=actor,
        subject_key=subject_key,
        payload={
            "fact_id": str(fact_id),
            "from": str(previous),
            "to": str(current),
            "corroboration_count": str(count),
            "has_trusted_source": has_trusted_source,
        },
    )
    await cur.execute(
        "UPDATE mnemos.semantic_facts SET trust = %s, updated_at = now() "
        "WHERE tenant_id = %s AND fact_id = %s",
        (str(current), tenant_id, fact_id),
    )
    log.info(
        "trust transition",
        extra={"fact_id": str(fact_id), "from": str(previous), "to": str(current), "count": count},
    )
    return previous, current


async def quarantine_stale_unverified(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    actor: str,
    ttl_days: int = DEFAULT_TTL_DAYS,
    now: datetime | None = None,
) -> int:
    """Move unverified facts that missed their corroboration window to
    `quarantined`, so they stop being ambient unverified noise available to
    `include_unverified=True` and start requiring explicit review.

    Never deletes — this is a trust-state change, and only the Warden holds
    DELETE. No legal-hold check: quarantine is not erasure, and a subject
    under hold is just as entitled to have its stale unverified claims flagged
    as one that is not.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=ttl_days)
    await cur.execute(
        "SELECT fact_id, subject_key FROM mnemos.semantic_facts "
        "WHERE tenant_id = %s AND trust = 'unverified' AND corroboration_count < 2 "
        "AND created_at < %s",
        (tenant_id, cutoff),
    )
    rows = await cur.fetchall()
    if not rows:
        return 0

    fact_ids = [r[0] for r in rows]
    await append_audit(
        cur,
        tenant_id,
        op=Op.QUARANTINE,
        actor=actor,
        subject_key=None,
        reason=f"no corroboration within {ttl_days}d",
        payload={"fact_ids": [str(f) for f in fact_ids], "count": len(fact_ids)},
    )
    await cur.execute(
        "UPDATE mnemos.semantic_facts "
        "SET trust = 'quarantined', quarantined_at = now(), "
        "    quarantine_reason = %s, updated_at = now() "
        "WHERE tenant_id = %s AND fact_id = ANY(%s)",
        (f"no corroboration within {ttl_days}d", tenant_id, fact_ids),
    )
    log.info(
        "quarantined stale unverified facts",
        extra={"tenant_id": str(tenant_id), "count": len(fact_ids)},
    )
    return len(fact_ids)


__all__ = [
    "DEFAULT_TTL_DAYS",
    "_max_independent_corroborations",
    "apply_trust_transition",
    "determine_trust",
    "quarantine_stale_unverified",
    "recompute_corroboration",
]
