"""Governance proposals (migration 006, `mnemos.governance_proposals`) —
PHASE_07 7.4's "the agent can ask; only a person can answer."

This module can INSERT a proposal and nothing else. There is no function
here that sets a proposal's `status` to `approved`, `rejected`, or
`executed` — those are decisions the Warden and a human admin make, through
`services/api`'s admin-scoped tools (dual control included), never here.
That is not merely a convention this module happens to follow: it holds
structurally, because `mnemos_custodian` does not depend on `mnemos_warden`
at all (`make no-warden-in-custodian` proves it statically, the same way
`make no-model-in-warden` proves the Warden holds no model — see the
Makefile). A Custodian that wanted to execute its own proposal would have to
import a package that is not on its dependency graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Json


class ProposalKind(StrEnum):
    FORGET = "forget"
    REVOKE = "revoke"
    QUARANTINE = "quarantine"
    HOLD = "hold"
    POLICY = "policy"


@dataclass(frozen=True)
class Proposal:
    tenant_id: UUID
    proposal_id: UUID
    proposed_by: str
    kind: ProposalKind
    target: str
    rationale: str
    evidence: dict[str, Any] | None
    created_at: datetime


async def propose(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    proposed_by: str,
    kind: ProposalKind,
    target: str,
    rationale: str,
    evidence: dict[str, Any] | None = None,
) -> Proposal:
    """File a proposal. `status` defaults to `pending` in the schema — this
    function has no `status=` parameter, on purpose; a caller that wants a
    proposal to start anywhere other than pending is asking for the wrong
    function.
    """
    proposal_id = uuid4()
    await cur.execute(
        "INSERT INTO mnemos.governance_proposals "
        "(tenant_id, proposal_id, proposed_by, kind, target, rationale, evidence) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "RETURNING created_at",
        (
            tenant_id,
            proposal_id,
            proposed_by,
            str(kind),
            target,
            rationale,
            Json(evidence) if evidence is not None else None,
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return Proposal(
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        proposed_by=proposed_by,
        kind=kind,
        target=target,
        rationale=rationale,
        evidence=evidence,
        created_at=row[0],
    )


__all__ = ["Proposal", "ProposalKind", "propose"]
