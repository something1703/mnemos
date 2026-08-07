"""Residency policy resolution — invariant 4.

A subject's home region comes from its residency policy, **not** from where the
writer happens to be running. That distinction is the whole of pillar I: an
agent in Frankfurt writing about a patient homed in India must not create an
EU-resident row just because the process is EU-resident.

The Warden owns enforcement of cross-border reads (Phase 06.2). This module owns
the question every write asks first: where does this subject live?
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg

from .models import Projection


@dataclass(frozen=True)
class ResidencyDecision:
    subject_key: str
    home_region: str
    projection: Projection
    pattern: str | None
    """The policy that matched, or None if the tenant default applied. Recorded
    in region_crossings so an auditor sees which rule permitted a crossing —
    "it was allowed" is not an answer without "by what"."""


def matches(subject_key: str, pattern: str) -> bool:
    """Prefix globbing only: `patient:eu:*`.

    Deliberately not regular expressions. A residency policy is read by people
    deciding whether data may leave a country, and a regex that nobody can
    confidently evaluate by eye is a policy nobody can confidently approve.
    """
    if pattern.endswith("*"):
        return subject_key.startswith(pattern[:-1])
    return subject_key == pattern


async def resolve(cur: psycopg.AsyncCursor, tenant_id: UUID, subject_key: str) -> ResidencyDecision:
    """Find the governing policy for a subject.

    Lowest `priority` wins, so a specific rule can be layered over a broad one
    without deleting it. Ties break on the longer (more specific) pattern.
    """
    await cur.execute(
        "SELECT subject_pattern, home_region, projection FROM mnemos.residency_policies "
        "WHERE tenant_id = %s ORDER BY priority, length(subject_pattern) DESC",
        (tenant_id,),
    )
    for pattern, home_region, projection in await cur.fetchall():
        if matches(subject_key, pattern):
            return ResidencyDecision(
                subject_key=subject_key,
                home_region=home_region,
                projection=Projection(projection),
                pattern=pattern,
            )

    await cur.execute(
        "SELECT default_region FROM mnemos.tenants WHERE tenant_id = %s", (tenant_id,)
    )
    row = await cur.fetchone()
    default_region = str(row[0]) if row else "us-east-1"

    # No policy is not an error: a tenant that has not declared residency gets
    # its default region and the most restrictive projection. Defaulting to
    # `derived` rather than `none` would silently permit cross-border sharing
    # for exactly the subjects nobody has thought about yet.
    return ResidencyDecision(
        subject_key=subject_key,
        home_region=default_region,
        projection=Projection.NONE,
        pattern=None,
    )


async def log_crossing(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    subject_key: str,
    from_region: str,
    to_region: str,
    decision: ResidencyDecision,
    allowed: bool,
    requested_by: str,
    denied_reason: str | None = None,
) -> None:
    """Record an attempted border crossing, permitted or refused.

    Denials are logged exactly as loudly as permissions. A residency system that
    records only its successes cannot answer the one question an auditor asks:
    what did you stop?
    """
    await cur.execute(
        """
        INSERT INTO mnemos.region_crossings
            (tenant_id, subject_key, from_region, to_region, projection,
             policy_applied, allowed, denied_reason, requested_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            tenant_id,
            subject_key,
            from_region,
            to_region,
            str(decision.projection),
            decision.pattern or "tenant-default",
            allowed,
            denied_reason,
            requested_by,
        ),
    )
