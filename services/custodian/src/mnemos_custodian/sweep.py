"""The sweep loop (PHASE_07 7.3): per skill, run its allowlisted Cloud MCP
tools, interpret the results using the skill's own `SKILL.md` body as triage
guidance, and persist structured findings.

**How a finding becomes recallable memory — and why this module does not
write `semantic_facts` directly.** Warn/critical findings are written back
into the fabric as ordinary episodes (`event_type="ops_finding"`) through a
write-scoped `remember()` call — the exact same path any other agent uses,
via `FactWriter`. From there the *existing* sleep-cycle pipeline (Phase 05)
turns them into `semantic_facts` and runs them through the *same*
corroboration gate every other fact goes through. A direct INSERT would
duplicate the one place that gate is implemented — exactly the second copy
`mnemos_engine.corroboration`'s docstring warns is how a security property
quietly stops meaning what it claims to.

**Two evidence classes, and why the distinction is load-bearing.** A sweep
produces both deterministic readings (`cluster_state_finding` here,
`check_backup_recency` in `cloud_api.py` — pure functions over control-plane
data, no model) and model interpretations. Measurements enter as
`source_trust='external'`, interpretations as `'agent'`. This is not a way
for the Custodian to promote itself: `max_independent_corroborations` matches
sessions against trust categories, so repeated `agent` sweeps stay pinned at
corroboration_count=1 and `unverified` forever, however often they agree —
verified live, and pinned by
`tests/sleep_cycle/test_consolidate.py::test_repeated_agent_only_sweeps_never_promote_themselves`.
What the split buys is that a measurement in one sweep and an interpretation
in another are genuinely different evidence, which is what PHASE_07 7.3's
"or a metric corroborates them" actually requires. Neither label is
self-granted — the API binds `system`/`operator` to an admin key
(`keys.py`'s `may_declare`), and both labels the Custodian can use are
untrusted on arrival.

Findings carry a `FindingCode` so the same condition seen twice produces
byte-identical claim text; `FindingCode`'s own docstring has the similarity
measurements that forced that design.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol
from uuid import UUID

import psycopg
from mnemos_engine.llm import ChatClient, LLMError

from . import allowlist, findings
from .cloud_api import CloudApiClient, backup_recency_finding
from .mcp_client import CustodianMcpClient
from .skills import Skill

log = logging.getLogger("mnemos.custodian.sweep")

_VALID_SEVERITIES = {"info", "warn", "critical"}

_CODE_CHOICES = ", ".join(f'"{c}"' for c in findings.FindingCode)


class FactWriter(Protocol):
    """The write-scoped path into semantic memory. A real implementation
    calls the Mnemos API's own `remember` MCP tool with a write-scoped key
    — `mnemos_readonly` (the Custodian's direct DB role) has no grant on
    `episodic_events` at all, by design (migration 011). This Protocol is
    what lets `run_sweep`'s orchestration be tested without a live API
    connection; `McpFactWriter` below is the real implementation.
    """

    async def remember_ops_finding(
        self,
        tenant_id: UUID,
        *,
        subject_key: str,
        content: str,
        session_id: UUID,
        source_trust: str,
    ) -> UUID: ...


class McpFactWriter:
    """`FactWriter` backed by a real `remember` MCP call, write-scoped."""

    def __init__(self, api_mcp_client: Any) -> None:
        self._client = api_mcp_client

    async def remember_ops_finding(
        self,
        tenant_id: UUID,
        *,
        subject_key: str,
        content: str,
        session_id: UUID,
        source_trust: str,
    ) -> UUID:
        result = await self._client.call_tool(
            "remember",
            {
                "subject_key": subject_key,
                "content": content,
                "event_type": "ops_finding",
                "source_trust": source_trust,
                "session_id": str(session_id),
            },
        )
        payload = result.structured_content or json.loads(result.content[0].text)
        return UUID(str(payload["event_id"]))


def _default_arguments(tool_name: str, *, database: str) -> dict[str, Any]:
    """The Cloud MCP tools this project allowlists split into two shapes:
    cluster-scoped (no arguments — `show_running_queries`, `get_cluster`,
    `list_databases`, `list_clusters`) and database-scoped (`list_tables`,
    `get_table_schema`). `show_statement`/`explain_query` need a `query`
    supplied by the caller, so `run_sweep` handles those separately rather
    than through this default-arguments table.
    """
    if tool_name in ("list_tables",):
        return {"database": database}
    return {}


def _render_tool_result(result: Any) -> Any:
    """MCP results carry both structured and text-block forms; prefer
    structured when the server provides it, since that is what a model
    should reason over rather than re-parsing prose."""
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    content = getattr(result, "content", None)
    if content:
        texts: list[str] = [
            t for block in content if (t := getattr(block, "text", None)) is not None
        ]
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except (json.JSONDecodeError, TypeError):
                return texts[0]
        return texts
    return None


def cluster_state_finding(tool_results: dict[str, Any]) -> findings.FindingDraft | None:
    """The cluster's own reported state, read as a field — no model involved.

    The interpreter also notices when a cluster is not RUNNING, and says so in
    its own words. This function says so in the *same* words every time, from
    the raw `get_cluster` payload, which is what makes the two genuinely
    independent evidence for the corroboration gate rather than one opinion
    stated twice: different pipeline, different `source_trust` (`external`
    here vs `agent` there), same canonical claim.

    Returns None when the state is RUNNING or simply absent — a check that
    invents a finding from missing data would be worse than no check.
    """
    payload = tool_results.get("get_cluster")
    if not isinstance(payload, dict):
        return None
    nested = payload.get("cluster")
    cluster: dict[str, Any] = nested if isinstance(nested, dict) else payload
    state = cluster.get("state")
    if not isinstance(state, str) or state.upper() == "RUNNING":
        return None
    return findings.FindingDraft(
        severity=findings.Severity.WARN,
        summary=f"Cluster state reported by the control plane is {state!r}, not 'RUNNING'.",
        evidence={"state": state, "cluster": cluster},
        skill_id="reviewing-cluster-health",
        tool_source=findings.ToolSource.MCP,
        code=findings.FindingCode.CLUSTER_NOT_RUNNING,
        measured=True,
        recommendation="Confirm provisioning completed; contact support if it stays non-RUNNING.",
    )


async def _interpret(chat: ChatClient, skill: Skill, tool_results: dict[str, Any]) -> list[Any]:
    """One model call per skill per sweep. The skill's own body is the
    system-prompt context (its triage guidance, written by CockroachDB's own
    engineers for exactly this kind of diagnosis); the raw tool results are
    the question. Output is data — a list of finding drafts — never an
    action, the same discipline `distill.py`/`revise.py` already hold to.
    """
    system = (
        "You are reviewing live CockroachDB Cloud diagnostics using the "
        f"'{skill.skill_id}' skill's own triage guidance below. Produce findings "
        "ONLY supported by the tool results given in the user message — never "
        "invent data not present in them. If the results show nothing "
        "noteworthy, return an empty findings list; a sweep with nothing to "
        "report is a normal, healthy outcome, not a failure to find something.\n\n"
        "Respond with JSON: "
        '{"findings": [{"severity": "info"|"warn"|"critical", "summary": "...", '
        '"evidence": {...}, "recommendation": "..." or null, "code": "..."}]}\n\n'
        "`code` classifies the CONDITION so that the same condition observed "
        "in a later sweep is recognised as the same one. Use exactly one of: "
        f'{_CODE_CHOICES}. Use "other" when the finding genuinely does not '
        "match any of them — do not force a fit, a wrong code is worse than "
        '"other".\n\n'
        f"--- {skill.skill_id} triage guidance ---\n{skill.body}"
    )
    user = json.dumps({"skill_id": skill.skill_id, "tool_results": tool_results}, default=str)

    try:
        raw = await chat.complete_json(system=system, user=user)
    except LLMError:
        log.warning("interpretation call failed for skill %s", skill.skill_id, exc_info=True)
        return []

    if not isinstance(raw, dict) or not isinstance(raw.get("findings"), list):
        log.warning("interpretation response malformed for skill %s: %r", skill.skill_id, raw)
        return []

    drafts: list[findings.FindingDraft] = []
    for item in raw["findings"]:
        if not isinstance(item, dict):
            continue
        severity = item.get("severity")
        summary = item.get("summary")
        if severity not in _VALID_SEVERITIES or not isinstance(summary, str) or not summary:
            log.warning("dropping malformed finding from %s: %r", skill.skill_id, item)
            continue
        evidence = item.get("evidence")
        try:
            code = findings.FindingCode(item.get("code") or "other")
        except ValueError:
            # A code outside the enum is the model inventing vocabulary, which
            # would silently split one condition into two identities. 'other'
            # is the honest fallback.
            log.info("unknown finding code %r from %s", item.get("code"), skill.skill_id)
            code = findings.FindingCode.OTHER
        drafts.append(
            findings.FindingDraft(
                severity=findings.Severity(severity),
                summary=summary,
                evidence=evidence if isinstance(evidence, dict) else {"raw": evidence},
                skill_id=skill.skill_id,
                tool_source=findings.ToolSource.MCP,
                code=code,
                recommendation=item.get("recommendation")
                if isinstance(item.get("recommendation"), str)
                else None,
            )
        )
    return drafts


async def run_sweep(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    trigger_source: findings.TriggerSource,
    trigger_detail: str | None,
    skills: dict[str, Skill],
    mcp: CustodianMcpClient,
    chat: ChatClient,
    fact_writer: FactWriter,
    session_id: UUID,
    database: str,
    cloud_api: CloudApiClient | None = None,
) -> UUID:
    """One full sweep across every given skill. Returns the run_id.

    Never raises on a single skill's or tool's failure — a sweep that dies
    on the first unreachable diagnostic would report nothing rather than
    partial, honestly-labeled coverage, which is worse. `finish_run`'s
    `status` reflects this: PARTIAL when anything was skipped, SUCCEEDED
    only when nothing was.

    `cloud_api` is optional — PHASE_07 7.5's control-plane facts (currently:
    backup recency) on top of the MCP-sourced findings above. `None` skips
    it cleanly rather than requiring every caller (including every test in
    `tests/custodian/test_sweep.py` that predates 7.5) to provide one.
    """
    run_id = await findings.start_run(
        cur, tenant_id, trigger_source=trigger_source, trigger_detail=trigger_detail
    )

    checks_run = 0
    checks_skipped = 0
    skipped_detail: dict[str, list[str]] = {}
    all_drafts: list[findings.FindingDraft] = []

    for skill_id, skill in skills.items():
        allowed = allowlist.tools_for(skill_id)
        tool_results: dict[str, Any] = {}

        for tool_name in sorted(allowed):
            try:
                result = await mcp.call_tool(
                    skill_id, tool_name, _default_arguments(tool_name, database=database)
                )
            except Exception as exc:
                checks_skipped += 1
                skipped_detail.setdefault(skill_id, []).append(f"{tool_name}: {exc}")
                log.info("skipped %s/%s: %s", skill_id, tool_name, exc)
                continue
            checks_run += 1
            tool_results[tool_name] = _render_tool_result(result)

        if not tool_results:
            continue

        # Deterministic readings first: they cost no tokens and they are the
        # half of the evidence that does not depend on the model's wording.
        measured = cluster_state_finding(tool_results)
        if measured is not None:
            all_drafts.append(measured)

        all_drafts.extend(await _interpret(chat, skill, tool_results))

    if cloud_api is not None:
        try:
            backup_finding = await backup_recency_finding(cloud_api)
        except Exception as exc:
            checks_skipped += 1
            skipped_detail.setdefault("cloud_api", []).append(f"backup_recency: {exc}")
            log.info("skipped cloud_api backup recency check: %s", exc)
        else:
            checks_run += 1
            if backup_finding is not None:
                all_drafts.append(backup_finding)

    persisted: list[findings.Finding] = []
    for draft in all_drafts:
        persisted.append(await findings.record_finding(cur, tenant_id, run_id, draft))

    for finding in persisted:
        if not finding.severity.promotable:
            continue
        # The canonical sentence when the condition is one we know, the
        # model's own wording only when it is not. This is what makes "the
        # same condition, seen twice" produce the same text twice, which is
        # what the corroboration gate needs (FindingCode's docstring has the
        # measurements that forced this).
        #
        # The recommendation is deliberately NOT appended. Measured live: with
        # it, four sweeps of the same condition produced one canonical claim
        # with four different advice sentences glued on, and only two of them
        # ever reinforced — the free text dragged similarity back under 0.92
        # and re-introduced exactly the drift the code was added to remove. It
        # also does not belong in a claim: "the cluster is not RUNNING" is a
        # statement about the world that can be corroborated, while "contact
        # support" is advice that cannot. The recommendation is already stored
        # on the finding row, which is where the console reads it from.
        content = finding.code.claim or finding.summary
        # A measurement and an interpretation are genuinely different kinds of
        # evidence, and the corroboration gate is built to notice exactly that:
        # `max_independent_corroborations` matches sessions against source-trust
        # categories, so a run of sweeps that all claim `agent` can never fill
        # more than the one `agent` slot and stays `unverified` forever — which
        # is correct, an agent must not promote itself by repetition. Labelling
        # the deterministic readings `external` is not a loophole around that:
        # `external` is untrusted on arrival too, and neither label is
        # self-granted (the API binds `system`/`operator` to an admin key —
        # keys.py's `may_declare`). It just stops the Custodian's own
        # measurements from being misfiled as its opinions.
        source_trust = "external" if finding.measured else "agent"
        await fact_writer.remember_ops_finding(
            tenant_id,
            subject_key=f"ops:{finding.skill_id}",
            content=content,
            session_id=session_id,
            source_trust=source_trust,
        )

    status = findings.RunStatus.SUCCEEDED if checks_skipped == 0 else findings.RunStatus.PARTIAL
    await findings.finish_run(
        cur,
        tenant_id,
        run_id,
        status=status,
        skills_run=len(skills),
        checks_run=checks_run,
        checks_skipped=checks_skipped,
        skipped_detail=skipped_detail or None,
    )
    return run_id


__all__ = ["FactWriter", "McpFactWriter", "run_sweep"]
