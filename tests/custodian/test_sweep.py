"""`run_sweep` end to end against a real local cluster (for `custodian_runs`/
`custodian_findings` persistence), a real in-process fake Cloud MCP server
(for the read-only + allowlist enforcement `mcp_client.py` already proves in
isolation), and a `ScriptedChat` (for the interpretation step) — the one
piece intentionally not live here is the model call itself, exactly the
scope `distill.py`'s and `revise.py`'s own tests hold to."""

from __future__ import annotations

import uuid

from mcp.server.mcpserver import MCPServer
from mnemos_custodian.findings import RunStatus, TriggerSource
from mnemos_custodian.mcp_client import CustodianMcpClient
from mnemos_custodian.skills import Skill
from mnemos_custodian.sweep import run_sweep

from .conftest import ScriptedChat, StubFactWriter


def _fake_cloud_mcp_server() -> MCPServer:
    server = MCPServer("fake-cloud-mcp")

    @server.tool(name="show_running_queries")
    def show_running_queries() -> list[dict]:
        return [{"query_id": "q1", "running_for": "12m", "query_preview": "SELECT * FROM big"}]

    @server.tool(name="show_statement")
    def show_statement(query: str, database: str | None = None) -> list[dict]:
        return [{"result": "ok"}]

    return server


def _one_skill(skill_id: str = "triaging-live-sql-activity") -> dict[str, Skill]:
    return {
        skill_id: Skill(
            skill_id=skill_id,
            description="Diagnoses live activity.",
            compatibility="Requires VIEWACTIVITY.",
            version="1.0",
            body="Look for queries running longer than 5 minutes and flag them.",
            references={"permissions.md": "..."},
        )
    }


async def test_sweep_persists_a_run_and_its_findings(db, tenant: uuid.UUID) -> None:
    chat = ScriptedChat(
        [
            {
                "findings": [
                    {
                        "severity": "warn",
                        "summary": "Query q1 has run for 12 minutes.",
                        "evidence": {"query_id": "q1"},
                        "recommendation": "Investigate or cancel it.",
                    }
                ]
            }
        ]
    )
    fact_writer = StubFactWriter()
    session_id = uuid.uuid4()

    async def run(cur):
        async with CustodianMcpClient(_fake_cloud_mcp_server()) as mcp:
            return await run_sweep(
                cur,
                tenant,
                trigger_source=TriggerSource.SCHEDULE,
                trigger_detail=None,
                skills=_one_skill(),
                mcp=mcp,
                chat=chat,
                fact_writer=fact_writer,
                session_id=session_id,
                database="mnemos",
            )

    run_id = await db.transaction(tenant, run, label="sweep")

    async def read_run(cur):
        await cur.execute(
            "SELECT status, skills_run, checks_run, checks_skipped FROM mnemos.custodian_runs "
            "WHERE tenant_id = %s AND run_id = %s",
            (tenant, run_id),
        )
        return await cur.fetchone()

    status, skills_run, checks_run, checks_skipped = await db.transaction(
        tenant, read_run, label="read_run", read_only=True
    )
    assert status == str(RunStatus.SUCCEEDED)
    assert skills_run == 1
    assert checks_run == 2  # show_running_queries + show_statement, both allowlisted and live
    assert checks_skipped == 0

    async def read_findings(cur):
        await cur.execute(
            "SELECT severity, summary, skill_id FROM mnemos.custodian_findings "
            "WHERE tenant_id = %s AND run_id = %s",
            (tenant, run_id),
        )
        return await cur.fetchall()

    rows = await db.transaction(tenant, read_findings, label="read_findings", read_only=True)
    assert len(rows) == 1
    assert rows[0][0] == "warn"
    assert "12 minutes" in rows[0][1]
    assert rows[0][2] == "triaging-live-sql-activity"


async def test_warn_finding_is_distilled_via_the_fact_writer(db, tenant: uuid.UUID) -> None:
    chat = ScriptedChat(
        [{"findings": [{"severity": "warn", "summary": "hot query", "evidence": {}}]}]
    )
    fact_writer = StubFactWriter()

    async def run(cur):
        async with CustodianMcpClient(_fake_cloud_mcp_server()) as mcp:
            return await run_sweep(
                cur,
                tenant,
                trigger_source=TriggerSource.MANUAL,
                trigger_detail=None,
                skills=_one_skill(),
                mcp=mcp,
                chat=chat,
                fact_writer=fact_writer,
                session_id=uuid.uuid4(),
                database="mnemos",
            )

    await db.transaction(tenant, run, label="sweep")
    assert len(fact_writer.remembered) == 1
    assert fact_writer.remembered[0]["subject_key"] == "ops:triaging-live-sql-activity"
    assert "hot query" in fact_writer.remembered[0]["content"]


async def test_info_finding_is_not_distilled(db, tenant: uuid.UUID) -> None:
    chat = ScriptedChat(
        [{"findings": [{"severity": "info", "summary": "nothing unusual", "evidence": {}}]}]
    )
    fact_writer = StubFactWriter()

    async def run(cur):
        async with CustodianMcpClient(_fake_cloud_mcp_server()) as mcp:
            return await run_sweep(
                cur,
                tenant,
                trigger_source=TriggerSource.SCHEDULE,
                trigger_detail=None,
                skills=_one_skill(),
                mcp=mcp,
                chat=chat,
                fact_writer=fact_writer,
                session_id=uuid.uuid4(),
                database="mnemos",
            )

    await db.transaction(tenant, run, label="sweep")
    assert fact_writer.remembered == []


async def test_malformed_model_response_yields_no_findings_and_does_not_raise(
    db, tenant: uuid.UUID
) -> None:
    chat = ScriptedChat([{"not_findings_at_all": True}])
    fact_writer = StubFactWriter()

    async def run(cur):
        async with CustodianMcpClient(_fake_cloud_mcp_server()) as mcp:
            return await run_sweep(
                cur,
                tenant,
                trigger_source=TriggerSource.SCHEDULE,
                trigger_detail=None,
                skills=_one_skill(),
                mcp=mcp,
                chat=chat,
                fact_writer=fact_writer,
                session_id=uuid.uuid4(),
                database="mnemos",
            )

    run_id = await db.transaction(tenant, run, label="sweep")

    async def read_findings(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.custodian_findings WHERE tenant_id = %s AND run_id = %s",
            (tenant, run_id),
        )
        return (await cur.fetchone())[0]

    count = await db.transaction(tenant, read_findings, label="read", read_only=True)
    assert count == 0


async def test_unreachable_tool_is_skipped_and_counted_not_silently_dropped(
    db, tenant: uuid.UUID
) -> None:
    """cockroachdb-sql's allowlist includes get_table_schema, which this
    fake server never registers — the same "allowlisted but not live"
    condition test_mcp_client.py proves raises; run_sweep must catch that
    and record it in skipped_detail, not let the whole sweep fail."""
    server = MCPServer("partial-fake")

    @server.tool(name="list_tables")
    def list_tables(database: str) -> list[str]:
        return ["episodic_events"]

    @server.tool(name="list_databases")
    def list_databases() -> list[str]:
        return ["mnemos"]

    skills = {
        "cockroachdb-sql": Skill(
            skill_id="cockroachdb-sql",
            description="Schema review.",
            compatibility="...",
            version="1.0",
            body="Check schema against anti-pattern rules." * 50,
            references={"EXAMPLES.md": "..."},
        )
    }
    chat = ScriptedChat([{"findings": []}])
    fact_writer = StubFactWriter()

    async def run(cur):
        async with CustodianMcpClient(server) as mcp:
            return await run_sweep(
                cur,
                tenant,
                trigger_source=TriggerSource.SCHEDULE,
                trigger_detail=None,
                skills=skills,
                mcp=mcp,
                chat=chat,
                fact_writer=fact_writer,
                session_id=uuid.uuid4(),
                database="mnemos",
            )

    run_id = await db.transaction(tenant, run, label="sweep")

    async def read_run(cur):
        await cur.execute(
            "SELECT status, checks_run, checks_skipped, skipped_detail "
            "FROM mnemos.custodian_runs WHERE tenant_id = %s AND run_id = %s",
            (tenant, run_id),
        )
        return await cur.fetchone()

    status, checks_run, checks_skipped, skipped_detail = await db.transaction(
        tenant, read_run, label="read_run", read_only=True
    )
    assert status == str(RunStatus.PARTIAL)
    assert checks_run == 2  # list_tables, list_databases
    # cockroachdb-sql also allowlists get_table_schema and explain_query,
    # neither registered on this fake server.
    assert checks_skipped == 2
    assert "cockroachdb-sql" in skipped_detail
    assert len(skipped_detail["cockroachdb-sql"]) == 2
