"""Read-side residency enforcement — invariant 4, the other direction.

`mnemos_engine.residency` resolves a subject's home region and lets `remember`
refuse a write from the wrong place. This module is the read-side counterpart:
a request originating in region X for a subject homed in region Y is served
**only** the projection its policy allows (none / derived / aggregate). Raw
episode content never crosses a boundary. Every crossing — permitted or
refused — is logged with the policy that decided it.

This lives in the Warden rather than the engine because it is a governance
decision, not a memory operation: `recall()` returns what exists, and this
module decides what of that a given requester is allowed to see.
"""

from __future__ import annotations

from uuid import UUID

import psycopg
from mnemos_engine import residency as engine_residency
from mnemos_engine.errors import ResidencyViolation
from mnemos_engine.models import Projection, RecallResult, ScoredFact


async def enforce_recall_projection(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    result: RecallResult,
    *,
    requester_region: str,
    requested_by: str,
) -> RecallResult:
    """Filter a RecallResult down to what `requester_region` may see.

    Runs per-fact, since two facts in one recall can be homed in different
    regions with different policies. `none` strips the fact entirely rather
    than truncating its content — a fact with no text is not a safer version
    of a fact, it is a different information leak (existence + metadata).
    """
    if not result.facts:
        return result

    kept: list[ScoredFact] = []
    for scored in result.facts:
        home = scored.fact.home_region
        if home == requester_region:
            kept.append(scored)
            continue

        decision = await engine_residency.resolve(cur, tenant_id, scored.fact.subject_key)

        if decision.projection is Projection.NONE:
            await engine_residency.log_crossing(
                cur,
                tenant_id,
                subject_key=scored.fact.subject_key,
                from_region=home,
                to_region=requester_region,
                decision=decision,
                allowed=False,
                requested_by=requested_by,
                denied_reason="policy permits no cross-border projection",
            )
            continue

        if decision.projection is Projection.AGGREGATE:
            # Aggregate-only policies permit statistics, not individual facts.
            # A single ScoredFact IS an individual fact, so it is withheld here;
            # aggregate views are a separate query surface (Phase 08 console),
            # not a degraded form of this one.
            await engine_residency.log_crossing(
                cur,
                tenant_id,
                subject_key=scored.fact.subject_key,
                from_region=home,
                to_region=requester_region,
                decision=decision,
                allowed=False,
                requested_by=requested_by,
                denied_reason="policy permits aggregate only, not individual facts",
            )
            continue

        # DERIVED: the fact (already a derived artifact of raw episodes) may
        # cross. Log the permitted crossing just as loudly as a denial.
        await engine_residency.log_crossing(
            cur,
            tenant_id,
            subject_key=scored.fact.subject_key,
            from_region=home,
            to_region=requester_region,
            decision=decision,
            allowed=True,
            requested_by=requested_by,
        )
        kept.append(scored)

    return result.model_copy(update={"facts": kept})


async def assert_episode_readable(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    subject_key: str,
    *,
    requester_region: str,
    requested_by: str,
) -> None:
    """Raw episode content NEVER crosses a border, regardless of projection
    policy — projections apply to derived facts only. Any attempt to read raw
    episode content from outside its home region is refused and logged."""
    decision = await engine_residency.resolve(cur, tenant_id, subject_key)
    if decision.home_region == requester_region:
        return

    await engine_residency.log_crossing(
        cur,
        tenant_id,
        subject_key=subject_key,
        from_region=decision.home_region,
        to_region=requester_region,
        decision=decision,
        allowed=False,
        requested_by=requested_by,
        denied_reason="raw episode content never crosses a jurisdictional boundary",
    )
    raise ResidencyViolation(subject_key, decision.home_region, requester_region)


class ResidencyReport:
    """Physical location of a subject's memory — the payload behind
    `db/scripts/where_is.py`, promoted here so the API and console call the
    same code the CLI does rather than reimplementing the query."""

    __slots__ = ("episode_regions", "fact_regions", "governing_policy", "subject_key")

    def __init__(
        self,
        subject_key: str,
        episode_regions: dict[str, int],
        fact_regions: dict[str, int],
        governing_policy: str | None,
    ) -> None:
        self.subject_key = subject_key
        self.episode_regions = episode_regions
        self.fact_regions = fact_regions
        self.governing_policy = governing_policy


async def where_is(cur: psycopg.AsyncCursor, tenant_id: UUID, subject_key: str) -> ResidencyReport:
    await cur.execute(
        "SELECT home_region, count(*) FROM mnemos.episodic_events "
        "WHERE tenant_id = %s AND subject_key = %s GROUP BY home_region",
        (tenant_id, subject_key),
    )
    episodes = {str(r[0]): int(r[1]) for r in await cur.fetchall()}

    await cur.execute(
        "SELECT home_region, count(*) FROM mnemos.semantic_facts "
        "WHERE tenant_id = %s AND subject_key = %s GROUP BY home_region",
        (tenant_id, subject_key),
    )
    facts = {str(r[0]): int(r[1]) for r in await cur.fetchall()}

    decision = await engine_residency.resolve(cur, tenant_id, subject_key)
    return ResidencyReport(
        subject_key=subject_key,
        episode_regions=episodes,
        fact_regions=facts,
        governing_policy=decision.pattern,
    )
