"""The REST views the console's Custodian screen reads.

Phase 08.6 needs sweep history, findings and governance proposals. None of
that was reachable over HTTP, and the alternative — letting the console open
its own database connection — would have put a second, unaudited read path
next to the one every other client uses.

The filter tests matter more than they look: both endpoints take optional
filters, and the obvious implementation assembles a WHERE clause from strings.
These pin the behaviour of the static-SQL version (nullable predicates), so a
later "simplification" back to string assembly has to keep passing them.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from mnemos_api.rest import build_rest_app

pytestmark = pytest.mark.security


@pytest.fixture
async def client(runtime):
    app = build_rest_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_run(runtime, tenant: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """One finished sweep with two findings — one measured, one interpreted."""
    run_id = uuid.uuid4()
    measured_id = uuid.uuid4()
    interpreted_id = uuid.uuid4()

    async def run(cur):
        await cur.execute(
            "INSERT INTO mnemos.custodian_runs (tenant_id, run_id, trigger_source, "
            "trigger_detail, status, skills_run, checks_run, checks_skipped, skipped_detail) "
            "VALUES (%s, %s, 'schedule', NULL, 'partial', 5, 9, 2, %s)",
            (tenant, run_id, '{"cockroachdb-sql": ["explain_query: nope"]}'),
        )
        for finding_id, severity, measured, code in (
            (measured_id, "warn", True, "cluster_not_running"),
            (interpreted_id, "info", False, "other"),
        ):
            await cur.execute(
                "INSERT INTO mnemos.custodian_findings (tenant_id, run_id, finding_id, "
                "severity, summary, evidence, skill_id, tool_source, measured, code) "
                "VALUES (%s, %s, %s, %s, %s, '{}', 'reviewing-cluster-health', 'mcp', %s, %s)",
                (tenant, run_id, finding_id, severity, f"a {severity} finding", measured, code),
            )

    await runtime.db.transaction(tenant, run, label="seed_custodian")
    return run_id, measured_id


async def test_runs_report_what_they_could_not_check(client, runtime, tenant, minted_keys):
    """Coverage honesty: a run's skipped count and detail are part of the
    response, not an internal field. A report listing only successes reads as
    full coverage."""
    run_id, _ = await _seed_run(runtime, tenant)
    response = await client.get(
        "/v1/custodian/runs",
        headers={"Authorization": f"Bearer {minted_keys['read']}"},
    )
    assert response.status_code == 200
    runs = response.json()["runs"]
    row = next(r for r in runs if r["run_id"] == str(run_id))
    assert row["status"] == "partial"
    assert row["checks_skipped"] == 2
    assert row["skipped_detail"] == {"cockroachdb-sql": ["explain_query: nope"]}


async def test_findings_expose_measured_separately_from_tool_source(
    client, runtime, tenant, minted_keys
):
    """`tool_source` and `measured` answer different questions — which surface
    produced the data, and whether a model interpreted it. Collapsing them
    would hide what makes a finding corroborable."""
    _, measured_id = await _seed_run(runtime, tenant)
    response = await client.get(
        "/v1/custodian/findings",
        headers={"Authorization": f"Bearer {minted_keys['read']}"},
    )
    assert response.status_code == 200
    findings = {f["finding_id"]: f for f in response.json()["findings"]}
    measured = findings[str(measured_id)]
    assert measured["measured"] is True
    assert measured["tool_source"] == "mcp"
    assert measured["code"] == "cluster_not_running"


async def test_findings_filter_by_severity_and_run(client, runtime, tenant, minted_keys):
    run_id, measured_id = await _seed_run(runtime, tenant)
    auth = {"Authorization": f"Bearer {minted_keys['read']}"}

    by_severity = await client.get("/v1/custodian/findings?severity=warn", headers=auth)
    ids = [f["finding_id"] for f in by_severity.json()["findings"]]
    assert str(measured_id) in ids
    assert all(f["severity"] == "warn" for f in by_severity.json()["findings"])

    by_run = await client.get(f"/v1/custodian/findings?run_id={run_id}", headers=auth)
    assert len(by_run.json()["findings"]) == 2
    assert all(f["run_id"] == str(run_id) for f in by_run.json()["findings"])

    # Both filters together, and a filter that matches nothing — the nullable
    # predicate has to behave for the empty case too, not just the happy one.
    both = await client.get(
        f"/v1/custodian/findings?run_id={run_id}&severity=critical", headers=auth
    )
    assert both.json()["findings"] == []


async def test_proposals_are_readable_and_filterable(client, runtime, tenant, minted_keys):
    proposal_id = uuid.uuid4()

    async def seed(cur):
        await cur.execute(
            "INSERT INTO mnemos.governance_proposals (tenant_id, proposal_id, proposed_by, "
            "kind, target, rationale, status) "
            "VALUES (%s, %s, 'agent:custodian', 'quarantine', 'subject:x', 'because', 'pending')",
            (tenant, proposal_id),
        )

    await runtime.db.transaction(tenant, seed, label="seed_proposal")
    auth = {"Authorization": f"Bearer {minted_keys['read']}"}

    response = await client.get("/v1/governance/proposals?status=pending", headers=auth)
    assert response.status_code == 200
    assert str(proposal_id) in [p["proposal_id"] for p in response.json()["proposals"]]

    none = await client.get("/v1/governance/proposals?status=executed", headers=auth)
    assert none.json()["proposals"] == []


async def test_custodian_views_require_a_key(client):
    """No credential, no sweep history. The Custodian's activity is tenant
    data like anything else."""
    for path in ("/v1/custodian/runs", "/v1/custodian/findings", "/v1/governance/proposals"):
        response = await client.get(path)
        assert response.status_code == 401
