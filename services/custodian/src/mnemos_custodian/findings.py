"""Persistence for `custodian_runs`/`custodian_findings` (migration 008).

Neither table is behind migration 010's `require_audit` trigger — only
`episodic_events`, `semantic_facts`, `skill_versions`, `legal_holds`, and
`residency_policies` are. The Custodian's own activity log is auditable in
the sense that it is itself a durable, queryable record (PHASE_07 7.2: "the
Custodian's own activity is auditable like everything else"), not in the
sense of needing an `append_audit` ticket — it carries no destructive or
governance-changing weight, so it is not wired into invariant 2's mechanism.

Writing here uses the `mnemos_readonly` role's direct grants (migration 011:
SELECT everywhere it's listed, INSERT on exactly `custodian_runs`,
`custodian_findings`, `governance_proposals`, UPDATE on `custodian_runs`).
Distilling a finding into `semantic_facts` is a different, write-scoped path
entirely — see `sweep.py`'s `FactWriter` — because `mnemos_readonly` has no
grant on that table at all, by design.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Json


class TriggerSource(StrEnum):
    SCHEDULE = "schedule"
    ALARM = "alarm"
    MANUAL = "manual"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"

    @property
    def promotable(self) -> bool:
        """Only warn/critical findings are distilled into semantic memory
        (PHASE_07 7.3) — an info-level finding is bookkeeping, not something
        worth an agent recalling later."""
        return self in (Severity.WARN, Severity.CRITICAL)


class ToolSource(StrEnum):
    MCP = "mcp"
    CCLOUD = "ccloud"


@dataclass(frozen=True)
class FindingDraft:
    """What the interpretation step produces, before it has a run_id or a
    finding_id — the model never sees or assigns either."""

    severity: Severity
    summary: str
    evidence: dict[str, Any]
    skill_id: str
    tool_source: ToolSource
    recommendation: str | None = None


@dataclass(frozen=True)
class Finding:
    """A persisted finding — a `FindingDraft` plus the identity DB assigns
    it. `fact_id` is set later, by whoever distills this into memory
    (`sweep.py`), via `mark_distilled`."""

    tenant_id: UUID
    run_id: UUID
    finding_id: UUID
    severity: Severity
    summary: str
    evidence: dict[str, Any]
    skill_id: str
    tool_source: ToolSource
    recommendation: str | None
    fact_id: UUID | None
    created_at: datetime


async def start_run(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    trigger_source: TriggerSource,
    trigger_detail: str | None = None,
) -> UUID:
    run_id = uuid4()
    await cur.execute(
        "INSERT INTO mnemos.custodian_runs "
        "(tenant_id, run_id, trigger_source, trigger_detail) VALUES (%s, %s, %s, %s)",
        (tenant_id, run_id, str(trigger_source), trigger_detail),
    )
    return run_id


async def finish_run(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    run_id: UUID,
    *,
    status: RunStatus,
    skills_run: int,
    checks_run: int,
    checks_skipped: int,
    skipped_detail: dict[str, Any] | None = None,
) -> None:
    """`skipped_detail` names WHICH diagnostics were skipped and why — a
    count alone answers 'how much coverage' but not 'coverage of what',
    and PHASE_07 7.1 is explicit that silent partial coverage is the
    failure mode to avoid."""
    await cur.execute(
        "UPDATE mnemos.custodian_runs SET finished_at = now(), status = %s, "
        "skills_run = %s, checks_run = %s, checks_skipped = %s, skipped_detail = %s "
        "WHERE tenant_id = %s AND run_id = %s",
        (
            str(status),
            skills_run,
            checks_run,
            checks_skipped,
            Json(skipped_detail) if skipped_detail is not None else None,
            tenant_id,
            run_id,
        ),
    )


async def record_finding(
    cur: psycopg.AsyncCursor, tenant_id: UUID, run_id: UUID, draft: FindingDraft
) -> Finding:
    finding_id = uuid4()
    await cur.execute(
        "INSERT INTO mnemos.custodian_findings "
        "(tenant_id, run_id, finding_id, severity, summary, evidence, "
        " recommendation, skill_id, tool_source) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING created_at",
        (
            tenant_id,
            run_id,
            finding_id,
            str(draft.severity),
            draft.summary,
            Json(draft.evidence),
            draft.recommendation,
            draft.skill_id,
            str(draft.tool_source),
        ),
    )
    row = await cur.fetchone()
    assert row is not None
    return Finding(
        tenant_id=tenant_id,
        run_id=run_id,
        finding_id=finding_id,
        severity=draft.severity,
        summary=draft.summary,
        evidence=draft.evidence,
        skill_id=draft.skill_id,
        tool_source=draft.tool_source,
        recommendation=draft.recommendation,
        fact_id=None,
        created_at=row[0],
    )


async def mark_distilled(
    cur: psycopg.AsyncCursor, tenant_id: UUID, run_id: UUID, finding_id: UUID, fact_id: UUID
) -> None:
    """Records which `semantic_facts` row a finding became, once distilled.
    Idempotent-safe to call twice with the same fact_id; not meant to be
    called with a different one for the same finding (a finding distills to
    exactly one fact)."""
    await cur.execute(
        "UPDATE mnemos.custodian_findings SET fact_id = %s "
        "WHERE tenant_id = %s AND run_id = %s AND finding_id = %s",
        (fact_id, tenant_id, run_id, finding_id),
    )


__all__ = [
    "Finding",
    "FindingDraft",
    "RunStatus",
    "Severity",
    "ToolSource",
    "TriggerSource",
    "finish_run",
    "mark_distilled",
    "record_finding",
    "start_run",
]
