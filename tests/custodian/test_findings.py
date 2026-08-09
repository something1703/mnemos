"""`custodian_runs`/`custodian_findings` persistence (migration 008) against
a real local cluster — connected as `root`, matching every other package's
DB-backed test suite; the `mnemos_readonly` role's actual grants are proven
separately in `tests/invariants/test_invariant_1_privileges.py`-style tests,
not re-proven per package."""

from __future__ import annotations

import uuid

from mnemos_custodian.findings import (
    FindingDraft,
    RunStatus,
    Severity,
    ToolSource,
    TriggerSource,
    finish_run,
    mark_distilled,
    record_finding,
    start_run,
)


async def test_start_run_creates_a_row_in_running_status(db, tenant: uuid.UUID) -> None:
    async def run(cur):
        run_id = await start_run(
            cur, tenant, trigger_source=TriggerSource.SCHEDULE, trigger_detail="every 6h"
        )
        await cur.execute(
            "SELECT status, trigger_source, trigger_detail FROM mnemos.custodian_runs "
            "WHERE tenant_id = %s AND run_id = %s",
            (tenant, run_id),
        )
        return await cur.fetchone()

    status, trigger_source, trigger_detail = await db.transaction(tenant, run, label="run")
    assert status == "running"
    assert trigger_source == "schedule"
    assert trigger_detail == "every 6h"


async def test_finish_run_records_coverage_honesty_fields(db, tenant: uuid.UUID) -> None:
    async def run(cur):
        run_id = await start_run(cur, tenant, trigger_source=TriggerSource.MANUAL)
        await finish_run(
            cur,
            tenant,
            run_id,
            status=RunStatus.PARTIAL,
            skills_run=5,
            checks_run=8,
            checks_skipped=2,
            skipped_detail={"cockroachdb-sql": ["explain_query: timeout"]},
        )
        await cur.execute(
            "SELECT status, skills_run, checks_run, checks_skipped, skipped_detail, finished_at "
            "FROM mnemos.custodian_runs WHERE tenant_id = %s AND run_id = %s",
            (tenant, run_id),
        )
        return await cur.fetchone()

    (
        status,
        skills_run,
        checks_run,
        checks_skipped,
        skipped_detail,
        finished_at,
    ) = await db.transaction(tenant, run, label="run")
    assert status == "partial"
    assert (skills_run, checks_run, checks_skipped) == (5, 8, 2)
    assert skipped_detail == {"cockroachdb-sql": ["explain_query: timeout"]}
    assert finished_at is not None


async def test_record_finding_persists_all_fields(db, tenant: uuid.UUID) -> None:
    draft = FindingDraft(
        severity=Severity.WARN,
        summary="3 queries running longer than 5 minutes",
        evidence={"query_ids": ["abc", "def"]},
        skill_id="triaging-live-sql-activity",
        tool_source=ToolSource.MCP,
        recommendation="Investigate application xyz",
    )

    async def run(cur):
        run_id = await start_run(cur, tenant, trigger_source=TriggerSource.SCHEDULE)
        finding = await record_finding(cur, tenant, run_id, draft)
        return run_id, finding

    run_id, finding = await db.transaction(tenant, run, label="run")
    assert finding.run_id == run_id
    assert finding.severity == Severity.WARN
    assert finding.summary == draft.summary
    assert finding.evidence == draft.evidence
    assert finding.recommendation == draft.recommendation
    assert finding.fact_id is None

    async def read(cur):
        await cur.execute(
            "SELECT severity, summary, evidence, recommendation, skill_id, tool_source, fact_id "
            "FROM mnemos.custodian_findings WHERE tenant_id = %s AND finding_id = %s",
            (tenant, finding.finding_id),
        )
        return await cur.fetchone()

    row = await db.transaction(tenant, read, label="read", read_only=True)
    assert row[0] == "warn"
    assert row[2] == draft.evidence
    assert row[6] is None


async def test_mark_distilled_sets_fact_id(db, tenant: uuid.UUID) -> None:
    draft = FindingDraft(
        severity=Severity.CRITICAL,
        summary="backups are stale",
        evidence={},
        skill_id="reviewing-cluster-health",
        tool_source=ToolSource.CCLOUD,
    )
    fact_id = uuid.uuid4()

    async def run(cur):
        run_id = await start_run(cur, tenant, trigger_source=TriggerSource.ALARM)
        finding = await record_finding(cur, tenant, run_id, draft)
        await mark_distilled(cur, tenant, run_id, finding.finding_id, fact_id)
        await cur.execute(
            "SELECT fact_id FROM mnemos.custodian_findings "
            "WHERE tenant_id = %s AND finding_id = %s",
            (tenant, finding.finding_id),
        )
        return await cur.fetchone()

    row = await db.transaction(tenant, run, label="run")
    assert row[0] == fact_id


async def test_severity_promotable_only_for_warn_and_critical() -> None:
    assert Severity.INFO.promotable is False
    assert Severity.WARN.promotable is True
    assert Severity.CRITICAL.promotable is True
