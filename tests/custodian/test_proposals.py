"""`propose()` against a real local cluster. The stronger claim this module
makes — that nothing in `mnemos_custodian` can ever resolve or execute a
proposal, because it cannot reach `mnemos_warden` at all — is proven
statically by `make no-warden-in-custodian`, not by a test here; a runtime
test could only ever show that a resolve function *happens* not to exist
today, not that one *cannot* be added without the static check catching it.
"""

from __future__ import annotations

import uuid

from mnemos_custodian.proposals import ProposalKind, propose


async def test_propose_persists_and_defaults_to_pending(db, tenant: uuid.UUID) -> None:
    async def run(cur):
        proposal = await propose(
            cur,
            tenant,
            proposed_by="custodian",
            kind=ProposalKind.QUARANTINE,
            target="patient:suspicious-source",
            rationale="Two corroborated findings suggest this source is poisoned.",
            evidence={"finding_ids": ["a", "b"]},
        )
        await cur.execute(
            "SELECT status, proposed_by, kind, target, rationale, evidence, decided_by "
            "FROM mnemos.governance_proposals WHERE tenant_id = %s AND proposal_id = %s",
            (tenant, proposal.proposal_id),
        )
        return proposal, await cur.fetchone()

    proposal, row = await db.transaction(tenant, run, label="run")
    status, proposed_by, kind, target, rationale, evidence, decided_by = row
    assert status == "pending"
    assert proposed_by == "custodian"
    assert kind == "quarantine"
    assert target == "patient:suspicious-source"
    assert rationale == proposal.rationale
    assert evidence == {"finding_ids": ["a", "b"]}
    assert decided_by is None


async def test_propose_without_evidence(db, tenant: uuid.UUID) -> None:
    async def run(cur):
        proposal = await propose(
            cur,
            tenant,
            proposed_by="custodian",
            kind=ProposalKind.HOLD,
            target="patient:x",
            rationale="Flagged by reviewing-cluster-health.",
        )
        await cur.execute(
            "SELECT evidence FROM mnemos.governance_proposals "
            "WHERE tenant_id = %s AND proposal_id = %s",
            (tenant, proposal.proposal_id),
        )
        return await cur.fetchone()

    row = await db.transaction(tenant, run, label="run")
    assert row[0] is None


async def test_every_proposal_kind_is_accepted_by_the_schema_check_constraint(
    db, tenant: uuid.UUID
) -> None:
    """ck_proposals_kind (migration 006) enumerates the same five kinds
    ProposalKind does — if the two ever drift, this is where it shows up as
    a real INSERT failure, not a silent mismatch."""

    async def run(cur):
        for kind in ProposalKind:
            await propose(
                cur, tenant, proposed_by="custodian", kind=kind, target="x", rationale="r"
            )

    await db.transaction(tenant, run, label="run")
