"""The MCP tool surface — the API design a judge and an agent both read.

Tool descriptions are written for *agent* consumption, which is a UX problem
more than a documentation one. Three rules applied throughout:

* **Say what the tool refuses to do, not just what it does.** An agent that
  knows `recall` hides unverified facts will not conclude memory is empty when
  it is merely untrusted.
* **Make irreversibility unmissable.** `forget`, `revoke_source`, and
  `set_legal_hold` state their consequences in the first line, and all three
  require an explicit `confirm=true` that the model has to choose to pass.
* **Never let a scope error read as a bug.** A write-scoped agent calling
  `forget` gets a clear "requires the 'admin' scope", not a stack trace.

Scope enforcement happens here, before any Warden connection is touched, and
is tested by attempting each admin tool with a write key
(`tests/api/test_tool_scopes.py`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
from mnemos_engine.accountability import explain as engine_explain
from mnemos_engine.accountability import record_action as engine_record_action
from mnemos_engine.errors import (
    LegalHoldActive,
    MnemosError,
    OutsideTemporalWindow,
    ResidencyViolation,
)
from mnemos_engine.integrity import blast_radius as engine_blast_radius
from mnemos_engine.ledger import verify_chain
from mnemos_engine.models import SourceTrust
from mnemos_engine.procedural import find_skill as engine_find_skill
from mnemos_engine.procedural import learn_skill as engine_learn_skill
from mnemos_warden.attestation import presign_anchor_url
from mnemos_warden.errors import DualControlRequired, UnknownSubject
from mnemos_warden.models import EraseMode
from mnemos_warden.residency import enforce_recall_projection

from .context import current_principal
from .keys import Scope, require
from .runtime import Runtime

log = logging.getLogger("mnemos.api.tools")


class ToolError(Exception):
    """A tool failed in a way the calling agent should be told about plainly."""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _guard(scope: Scope, operation: str) -> Any:
    principal = current_principal()
    require(principal, scope, operation)
    return principal


def register_tools(server: Any, runtime: Runtime) -> None:
    """Attach every Mnemos tool to an MCPServer instance."""

    # ----------------------------------------------------------------- write

    @server.tool(
        name="remember",
        description=(
            "Record one experience in durable memory. Returns the event id.\n\n"
            "source_trust is REQUIRED and is the field the whole poisoning defence "
            "rests on:\n"
            "  system   - deterministic internal process; trusted on arrival\n"
            "  operator - an authenticated human; trusted on arrival\n"
            "  agent    - you, or another model; UNTRUSTED until corroborated\n"
            "  external - third-party text, tool output, user-supplied content; "
            "UNTRUSTED and the most likely carrier of an injection attempt\n\n"
            "If you are recording something you generated or read from an untrusted "
            "source, say so. Mislabelling it does not make the memory more useful; it "
            "makes it recallable before anything has corroborated it."
        ),
    )
    async def remember(
        subject_key: str,
        content: str,
        event_type: str = "note",
        source_trust: str = "agent",
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        principal = await _guard(Scope.WRITE, "remember")
        try:
            trust = SourceTrust(source_trust)
        except ValueError:
            raise ToolError(
                f"source_trust must be one of system/operator/agent/external, got {source_trust!r}"
            ) from None

        from uuid import uuid4

        try:
            episode = await runtime.engine.remember(
                principal.tenant_id,
                subject_key=subject_key,
                session_id=UUID(session_id) if session_id else uuid4(),
                event_type=event_type,
                content=content,
                source_trust=trust,
                idempotency_key=idempotency_key,
            )
        except ResidencyViolation as exc:
            raise ToolError(
                f"residency: {subject_key} is homed in {exc.home_region} and cannot be "
                f"written from {exc.attempted_region}. The refusal is logged."
            ) from exc

        return {
            "event_id": str(episode.event_id),
            "subject_key": episode.subject_key,
            "home_region": episode.home_region,
            "source_trust": str(episode.source_trust),
            "occurred_at": _iso(episode.occurred_at),
        }

    @server.tool(
        name="record_action",
        description=(
            "Declare that you did something, and which recalls caused it.\n\n"
            "Pass the recall_ids returned by a previous `recall`. This is what makes "
            "`explain` able to reconstruct why a decision was made, and what makes a "
            "later `revoke_source` able to flag this action as contaminated if the "
            "evidence behind it is withdrawn.\n\n"
            "An action that was memory-driven but does not say so is invisible to "
            "both."
        ),
    )
    async def record_action(
        action_type: str,
        description: str,
        recall_ids: list[str],
        subject_key: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        principal = await _guard(Scope.WRITE, "record_action")

        async def run(cur: Any) -> UUID:
            return await engine_record_action(
                cur,
                principal.tenant_id,
                action_type=action_type,
                description=description,
                recall_ids=[UUID(r) for r in recall_ids],
                actor=f"agent:{principal.label}",
                session_id=UUID(session_id) if session_id else None,
                subject_key=subject_key,
            )

        action_id = await runtime.db.transaction(principal.tenant_id, run, label="record_action")
        return {"action_id": str(action_id)}

    @server.tool(
        name="learn_skill",
        description=(
            "Store a reusable playbook, versioned.\n\n"
            "IMPORTANT: a skill you author yourself lands QUARANTINED and will not be "
            "returned by find_skill until independently corroborated. This is "
            "deliberate — an agent that can write its own procedure and immediately "
            "execute it has no security boundary at all. Cite the fact_ids the "
            "playbook rests on so revocation can reach it later."
        ),
    )
    async def learn_skill(
        name: str,
        playbook: str,
        task_description: str,
        source_trust: str = "agent",
        fact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        principal = await _guard(Scope.WRITE, "learn_skill")

        async def run(cur: Any) -> Any:
            return await engine_learn_skill(
                cur,
                principal.tenant_id,
                name=name,
                playbook=playbook,
                task_description=task_description,
                source_trust=SourceTrust(source_trust),
                embedder=runtime.embedder,
                envelope=runtime.engine.envelope,
                actor=f"agent:{principal.label}",
                fact_ids=[UUID(f) for f in (fact_ids or [])],
            )

        version = await runtime.db.transaction(principal.tenant_id, run, label="learn_skill")
        return {
            "skill_id": str(version.skill_id),
            "version": version.version,
            "trust": str(version.trust),
            "executable": version.is_executable,
            "note": (
                "quarantined pending corroboration — find_skill will not return it yet"
                if not version.is_executable
                else "trusted on arrival"
            ),
        }

    # ------------------------------------------------------------------ read

    @server.tool(
        name="recall",
        description=(
            "Retrieve relevant memory: hybrid vector + full-text search, fused by rank.\n\n"
            "Facts that are unverified or quarantined are EXCLUDED by default. The "
            "response reports how many were withheld — if that number is high and "
            "results are thin, the correct reading is 'nothing is trusted yet', not "
            "'nothing is known'. A rising withheld count is also the leading "
            "indicator of a poisoning attempt.\n\n"
            "Separately, facts homed in another jurisdiction may be withheld by "
            "residency policy regardless of trust — reported as residency_withheld. "
            "That count existing and staying at zero for a normal query is expected; "
            "it existing and being nonzero means this instance is not the right place "
            "to ask about that subject, not that the memory does not exist.\n\n"
            "Returns recall_ids; pass them to record_action if you act on what you "
            "were told. Contested facts are returned as pairs with both sides' "
            "evidence rather than silently resolved."
        ),
    )
    async def recall(
        query: str,
        subject_key: str | None = None,
        k: int = 8,
        include_unverified: bool = False,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        principal = await _guard(Scope.READ, "recall")
        result = await runtime.engine.recall(
            principal.tenant_id,
            query,
            subject_key=subject_key,
            k=k,
            include_unverified=include_unverified,
            session_id=UUID(session_id) if session_id else None,
        )
        result, residency_withheld = await _apply_residency(runtime, principal, result)
        payload = _render_recall(result)
        payload["residency_withheld"] = residency_withheld
        return payload

    @server.tool(
        name="recall_as_of",
        description=(
            "What memory would have returned at a past instant — the facts as they "
            "stood then, before any later supersession or revocation.\n\n"
            "Read-only: asking what was known does not reinforce anything or create a "
            "recall record. Bounded by the cluster's MVCC retention; a timestamp older "
            "than that fails loudly with the real boundary rather than silently "
            "answering from the present, which would look correct and be wrong.\n\n"
            "as_of must be ISO-8601, e.g. 2026-08-09T10:30:00Z"
        ),
    )
    async def recall_as_of(
        query: str,
        as_of: str,
        subject_key: str | None = None,
        k: int = 8,
    ) -> dict[str, Any]:
        principal = await _guard(Scope.READ, "recall_as_of")
        try:
            when = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError(f"as_of must be ISO-8601; got {as_of!r}") from None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)

        try:
            result = await runtime.engine.recall_as_of(
                principal.tenant_id, query, when, subject_key=subject_key, k=k
            )
        except OutsideTemporalWindow as exc:
            raise ToolError(
                f"{as_of} is outside the temporal window. History reaches back only to "
                f"{exc.earliest.isoformat()} (gc.ttlseconds={exc.gc_ttl_seconds}). "
                "Subjects under legal hold get an extended window."
            ) from exc

        result, residency_withheld = await _apply_residency(runtime, principal, result)
        payload = _render_recall(result)
        payload["residency_withheld"] = residency_withheld
        payload["as_of"] = as_of
        payload["note"] = "historical view; nothing was reinforced or logged"
        return payload

    @server.tool(
        name="explain",
        description=(
            "The verifiable causal chain behind one action: which recalls caused it, "
            "what those facts said AT THAT MOMENT, their provenance back to source "
            "episodes, and the covering ledger checkpoint.\n\n"
            "Reports historical state, not current — a fact that was trusted when used "
            "and is quarantined today shows both. If the action was influenced by "
            "memory that has since been revoked, it says so explicitly.\n\n"
            "When the covering checkpoint has been anchored to S3 Object Lock, the "
            "response includes anchor_presigned_url: a time-limited link (no AWS "
            "credentials needed) to fetch and independently verify that anchored root, "
            "not just trust the anchor_uri string.\n\n"
            "Source episode CONTENT is never included, only its hash: a deposition may "
            "legitimately cross a jurisdiction the underlying record may not."
        ),
    )
    async def explain(action_id: str) -> dict[str, Any]:
        principal = await _guard(Scope.READ, "explain")

        async def run(cur: Any) -> Any:
            return await engine_explain(cur, principal.tenant_id, UUID(action_id))

        deposition = await runtime.db.transaction(
            principal.tenant_id, run, label="explain", read_only=True
        )
        if deposition is None:
            raise ToolError(f"no action {action_id} in this tenant")

        anchor_presigned_url = None
        if deposition.anchor_uri and runtime.s3 and runtime.settings.anchor_bucket:
            # A judge holding this deposition should be able to fetch and
            # verify the anchored root without ever holding an AWS credential
            # of their own — the raw s3:// URI alone cannot do that, since the
            # bucket is deliberately private (ADR-013). Best-effort: a signing
            # failure here must not fail the whole deposition, since
            # everything a caller actually needs to verify the chain
            # themselves (checkpoint_seq, merkle_root, anchor_uri) is already
            # in the response above.
            try:
                anchor_presigned_url = presign_anchor_url(
                    s3=runtime.s3,
                    bucket=runtime.settings.anchor_bucket,
                    tenant_id=principal.tenant_id,
                    checkpoint_seq=deposition.checkpoint_seq,
                )
            except Exception:
                log.warning("failed to presign anchor URL for deposition", exc_info=True)

        return {
            "action_id": str(deposition.action_id),
            "action_type": deposition.action_type,
            "description": deposition.description,
            "declared_at": _iso(deposition.declared_at),
            "contaminated": deposition.contaminated,
            "contamination_note": deposition.contamination_note,
            "checkpoint_seq": deposition.checkpoint_seq,
            "merkle_root": deposition.merkle_root,
            "anchor_uri": deposition.anchor_uri,
            "anchor_presigned_url": anchor_presigned_url,
            "anchor_presigned_url_expires_in": 3600 if anchor_presigned_url else None,
            "anchored": deposition.anchor_uri is not None,
            "facts": [
                {
                    "fact_id": str(f.fact_id),
                    "subject_key": f.subject_key,
                    "trust_at_recall": f.trust_at_recall,
                    "trust_now": f.trust_now,
                    "changed_since": f.changed_since,
                    "revoked_since": f.revoked_since,
                    "superseded_since": f.superseded_since,
                    "score_at_recall": f.score_at_recall,
                    "provenance": [
                        {
                            "event_id": str(e.event_id),
                            "event_type": e.event_type,
                            "source_trust": e.source_trust,
                            "content_hash": e.content_hash,
                            "occurred_at": _iso(e.occurred_at),
                        }
                        for e in f.provenance
                    ],
                }
                for f in deposition.facts
            ],
            "summary": deposition.summary(),
        }

    @server.tool(
        name="blast_radius",
        description=(
            "Given a source episode, compute everything it touched — transitively.\n\n"
            "Follows facts derived from it, skills citing those facts, recalls that "
            "returned them, actions declared on those recalls, and episodes an agent "
            "wrote AFTER acting on them (the laundering step, where contamination "
            "hides behind impeccable-looking provenance).\n\n"
            "PREVIEW ONLY — changes nothing. Use revoke_source to act on it."
        ),
    )
    async def blast_radius(source_event_ids: list[str]) -> dict[str, Any]:
        principal = await _guard(Scope.READ, "blast_radius")

        async def run(cur: Any) -> Any:
            return await engine_blast_radius(
                cur, principal.tenant_id, [UUID(e) for e in source_event_ids]
            )

        radius = await runtime.db.transaction(
            principal.tenant_id, run, label="blast_radius", read_only=True
        )
        return {
            "summary": radius.summary(),
            "counts": radius.manifest()["counts"],
            "max_depth": radius.max_depth_reached,
            "truncated": radius.truncated,
            "facts": [
                {
                    "fact_id": str(f.fact_id),
                    "subject_key": f.subject_key,
                    "trust": f.trust,
                    "depth": f.depth,
                }
                for f in radius.facts
            ],
            "skills": [
                {"skill_id": str(s.skill_id), "name": s.name, "version": s.version}
                for s in radius.skills
            ],
            "action_ids": [str(a) for a in radius.action_ids],
        }

    @server.tool(
        name="find_skill",
        description=(
            "Find a learned playbook matching a task, by semantic similarity.\n\n"
            "Only returns EXECUTABLE skills. Quarantined ones — including every "
            "agent-authored skill that has not been corroborated — are withheld, and "
            "that is not a bug you should work around."
        ),
    )
    async def find_skill(task: str, k: int = 3) -> dict[str, Any]:
        principal = await _guard(Scope.READ, "find_skill")

        async def run(cur: Any) -> Any:
            return await engine_find_skill(
                cur,
                principal.tenant_id,
                task,
                embedder=runtime.embedder,
                envelope=runtime.engine.envelope,
                k=k,
            )

        skills = await runtime.db.transaction(principal.tenant_id, run, label="find_skill")
        return {
            "skills": [
                {
                    "skill_id": str(s.skill_id),
                    "name": s.name,
                    "version": s.version,
                    "trust": str(s.trust),
                    "playbook": s.playbook,
                    "successes": s.successes,
                    "failures": s.failures,
                    "fitness": round(s.fitness, 3),
                }
                for s in skills
            ]
        }

    @server.tool(
        name="where_is",
        description=(
            "Which jurisdiction a subject's memory physically lives in, and the "
            "residency policy governing it. Raw content never crosses a border; only "
            "policy-approved derived projections do."
        ),
    )
    async def where_is(subject_key: str) -> dict[str, Any]:
        principal = await _guard(Scope.READ, "where_is")
        report = await runtime.warden.where_is(principal.tenant_id, subject_key)
        return {
            "subject_key": report.subject_key,
            "episode_regions": report.episode_regions,
            "fact_regions": report.fact_regions,
            "governing_policy": report.governing_policy,
        }

    @server.tool(
        name="memory_stats",
        description=(
            "Tier counts, trust distribution, and this instance's actual security "
            "posture. The trust distribution is the number to watch: a rising "
            "unverified count is what a poisoning attempt looks like from the outside."
        ),
    )
    async def memory_stats() -> dict[str, Any]:
        principal = await _guard(Scope.READ, "memory_stats")

        async def run(cur: Any) -> dict[str, Any]:
            await cur.execute(
                "SELECT count(*) FROM mnemos.episodic_events WHERE tenant_id = %s",
                (principal.tenant_id,),
            )
            episodes = int((await cur.fetchone())[0])
            await cur.execute(
                "SELECT trust, count(*) FROM mnemos.semantic_facts "
                "WHERE tenant_id = %s GROUP BY trust",
                (principal.tenant_id,),
            )
            trust = {str(r[0]): int(r[1]) for r in await cur.fetchall()}
            await cur.execute(
                "SELECT count(*) FROM mnemos.audit_chain WHERE tenant_id = %s",
                (principal.tenant_id,),
            )
            chain = int((await cur.fetchone())[0])
            return {"episodes": episodes, "facts_by_trust": trust, "chain_entries": chain}

        stats = await runtime.db.transaction(
            principal.tenant_id, run, label="memory_stats", read_only=True
        )
        stats["posture"] = runtime.describe_posture()
        return stats

    @server.tool(
        name="verify_ledger",
        description=(
            "Recompute every hash in the tenant's audit chain. Returns VALID or the "
            "exact first broken link.\n\n"
            "Note the limit honestly: this checks the chain against itself. An "
            "attacker with database write access who rewrites a shard AND its "
            "checkpoint consistently passes this. Catching that requires comparing "
            "against the root anchored in S3 Object Lock (mnemos-attest verify)."
        ),
    )
    async def verify_ledger() -> dict[str, Any]:
        principal = await _guard(Scope.READ, "verify_ledger")

        async def run(cur: Any) -> Any:
            return await verify_chain(cur, principal.tenant_id)

        result = await runtime.db.transaction(
            principal.tenant_id, run, label="verify_ledger", read_only=True
        )
        return {
            "valid": result.valid,
            "entries_checked": result.entries_checked,
            "shards_checked": result.shards_checked,
            "checkpoints_checked": result.checkpoints_checked,
            "broken_at": list(result.broken_at) if result.broken_at else None,
            "detail": result.detail,
            "caveat": (
                "no checkpoints exist, so a consistent whole-shard rewrite would not be detectable"
                if result.checkpoints_checked == 0
                else None
            ),
        }

    # ----------------------------------------------------------------- admin

    @server.tool(
        name="forget",
        description=(
            "IRREVERSIBLE. Erase a subject's memory. Requires the 'admin' scope and "
            "confirm=true.\n\n"
            "Modes:\n"
            "  redact     - tombstone content, keep the row and its audit history\n"
            "  forget     - delete episodes, facts, vectors and provenance atomically\n"
            "  quarantine - retain everything, revoke it from recall (REVERSIBLE)\n"
            "  shred      - forget, plus destroy the tenant's encryption key so "
            "backup and MVCC copies become unreadable. TENANT-WIDE, not just this "
            "subject.\n\n"
            "Refused outright if the subject is under legal hold — the refusal cites "
            "the matter reference. Call with confirm=false first to preview exactly "
            "what would be destroyed.\n\n"
            "If this tenant has dual control enabled, a confirm=true call from one "
            "admin key records an approval and is refused (DUAL CONTROL); a SECOND, "
            "DISTINCT admin key calling this again for the same subject_key and mode "
            "is what actually executes it. The approval expires after 15 minutes."
        ),
    )
    async def forget(
        subject_key: str,
        reason: str,
        mode: str = "forget",
        confirm: bool = False,
    ) -> dict[str, Any]:
        principal = await _guard(Scope.ADMIN, "forget")
        try:
            erase_mode = EraseMode(mode)
        except ValueError:
            raise ToolError(f"mode must be redact/forget/quarantine/shred, got {mode!r}") from None

        try:
            preview = await runtime.warden.preview_erasure(
                principal.tenant_id, subject_key, erase_mode
            )
        except UnknownSubject as exc:
            raise ToolError(str(exc)) from exc

        if preview.is_blocked:
            hold = preview.blocked_by_hold
            assert hold is not None
            raise ToolError(
                f"REFUSED: {subject_key} is under legal hold {hold.matter_reference} "
                f"(placed by {hold.placed_by}). Erasure is blocked until the hold is "
                "released. Nothing was modified."
            )

        if not confirm:
            return {
                "preview": True,
                "mode": erase_mode.value,
                "would_remove": {
                    "episodes": preview.episode_count,
                    "facts": preview.fact_count,
                    "provenance_edges": preview.provenance_edge_count,
                },
                "next": "call again with confirm=true to execute",
            }

        method = {
            EraseMode.REDACT: runtime.warden.redact,
            EraseMode.FORGET: runtime.warden.forget,
            EraseMode.QUARANTINE: runtime.warden.quarantine,
            EraseMode.SHRED: runtime.warden.shred,
        }[erase_mode]

        try:
            result = await method(
                principal.tenant_id,
                subject_key,
                actor=f"agent:{principal.label}",
                reason=reason,
                confirm=True,
                admin_key_id=principal.key_id,
                admin_label=principal.label,
            )
        except DualControlRequired as exc:
            raise ToolError(
                f"DUAL CONTROL: {exc.first_approver} has approved this {mode} of "
                f"{subject_key!r}; a second, distinct admin key must call this again "
                "with confirm=true to execute it. Nothing was modified."
            ) from exc
        except LegalHoldActive as exc:
            raise ToolError(
                f"REFUSED: legal hold {exc.matter_reference} placed by {exc.placed_by}"
            ) from exc

        return {
            "executed": True,
            "mode": result.mode.value,
            "episodes_removed": result.episodes_removed,
            "facts_removed": result.facts_removed,
            "key_destroyed": result.key_destroyed,
            "executed_at": _iso(result.executed_at),
        }

    @server.tool(
        name="revoke_source",
        description=(
            "Revoke a poisoned source and re-evaluate everything it touched, "
            "transitively, in ONE transaction. Requires the 'admin' scope and "
            "confirm=true.\n\n"
            "A fact with no support left once the revoked source's evidence is set "
            "aside is quarantined (quarantined_fact_ids); a fact ALSO corroborated by "
            "genuinely independent, non-revoked evidence survives, demoted to "
            "whatever that remaining evidence actually earns (demoted_fact_ids) — "
            "over-revocation is as much a bug as under-revocation. Skills citing a "
            "quarantined fact are quarantined outright. Nothing is deleted: a "
            "revocation says the evidence should not be trusted, not that the record "
            "of what happened should vanish. Affected actions are marked "
            "contaminated, so explain() on them reports it afterwards.\n\n"
            "Call with confirm=false to see the blast radius first. Subject to dual "
            "control the same way forget is, if this tenant has it enabled."
        ),
    )
    async def revoke_source(
        source_event_ids: list[str],
        reason: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        principal = await _guard(Scope.ADMIN, "revoke_source")
        ids = [UUID(e) for e in source_event_ids]

        if not confirm:
            radius = await runtime.warden.preview_revoke_source(principal.tenant_id, ids)
            return {
                "preview": True,
                "summary": radius.summary(),
                "counts": radius.manifest()["counts"],
                "next": "call again with confirm=true to revoke",
            }

        try:
            record = await runtime.warden.revoke_source(
                principal.tenant_id,
                ids,
                actor=f"agent:{principal.label}",
                reason=reason,
                confirm=True,
                admin_key_id=principal.key_id,
                admin_label=principal.label,
            )
        except DualControlRequired as exc:
            raise ToolError(
                f"DUAL CONTROL: {exc.first_approver} has approved revoking these "
                "sources; a second, distinct admin key must call this again with "
                "confirm=true to execute it. Nothing was modified."
            ) from exc
        return {
            "executed": True,
            "revocation_id": str(record.revocation_id),
            "counts": record.radius_manifest.get("counts"),
            "quarantined_fact_ids": record.radius_manifest.get("quarantined_fact_ids", []),
            "demoted_fact_ids": record.radius_manifest.get("demoted_fact_ids", []),
            "created_at": _iso(record.created_at),
        }

    @server.tool(
        name="set_legal_hold",
        description=(
            "Block erasure of a subject, citing an external matter. Requires the "
            "'admin' scope and confirm=true.\n\n"
            "A held subject cannot be forgotten or TTL-expired. Any erasure attempt "
            "fails loudly with this matter reference. Use when data must be preserved "
            "regardless of a deletion request — a system that always deletes on "
            "request is not compliant, only obedient. Subject to dual control the "
            "same way forget is, if this tenant has it enabled."
        ),
    )
    async def set_legal_hold(
        subject_key: str,
        matter_reference: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        principal = await _guard(Scope.ADMIN, "set_legal_hold")

        try:
            hold = await runtime.warden.set_legal_hold(
                principal.tenant_id,
                subject_key,
                matter_reference=matter_reference,
                placed_by=f"agent:{principal.label}",
                confirm=confirm,
                admin_key_id=principal.key_id if confirm else None,
                admin_label=principal.label if confirm else None,
            )
        except DualControlRequired as exc:
            raise ToolError(
                f"DUAL CONTROL: {exc.first_approver} has approved this hold on "
                f"{subject_key!r}; a second, distinct admin key must call this "
                "again with confirm=true to execute it. Nothing was modified."
            ) from exc
        return {
            "hold_id": str(hold.hold_id),
            "subject_key": hold.subject_key,
            "matter_reference": hold.matter_reference,
            "placed_at": _iso(hold.placed_at),
            "active": hold.is_active,
        }

    # NB: MCPServer.list_tools() is async in the 2.0 SDK, so it is not called
    # here — registration is synchronous and complete by this point.
    log.info("MCP tools registered")


async def _apply_residency(runtime: Runtime, principal: Any, result: Any) -> tuple[Any, int]:
    """Filter a RecallResult down to what this deployment's region may serve,
    before it is ever rendered to JSON. Returns `(filtered_result, withheld)`.

    `enforce_recall_projection` (packages/warden) is a real, unit-tested
    implementation of invariant 4's read side — but until this call existed,
    nothing in the request path actually invoked it. `recall()` returned every
    matching fact's full text regardless of the fact's home region, which
    meant a single-region deployment happened to look correct only because it
    never asked the question a multi-region one would answer wrong. This is
    the one call site that turns the module from a unit-tested capability into
    an enforced one.

    Runs in its own transaction, separate from `engine.recall()`'s: a crossing
    denial has to be logged (an INSERT), and `recall_as_of`'s own transaction
    is `AS OF SYSTEM TIME` and read-only, which cannot write at all. The
    residency *policy* applied is always the current one — a policy change
    takes effect immediately on what can be served, regardless of when the
    underlying fact was originally recalled.

    The withheld count exists for the same reason `unverified_withheld`
    exists: an agent that sees an empty `facts` list has to be able to tell
    "nothing matched" apart from "something matched and was not servable
    here" — the trust-gate version of that distinction already shipped in
    Phase 03; a residency-filtered result deserves the same legibility, not a
    silent empty list that reads as "nothing is known".
    """
    before = len(result.facts)

    async def run(cur: psycopg.AsyncCursor) -> Any:
        return await enforce_recall_projection(
            cur,
            principal.tenant_id,
            result,
            requester_region=runtime.settings.region,
            requested_by=f"key:{principal.key_id}",
        )

    filtered = await runtime.db.transaction(principal.tenant_id, run, label="enforce_residency")
    return filtered, before - len(filtered.facts)


def _render_recall(result: Any) -> dict[str, Any]:
    return {
        "facts": [
            {
                "fact_id": str(s.fact.fact_id),
                "subject_key": s.fact.subject_key,
                "fact_kind": s.fact.fact_kind,
                "text": s.fact.text,
                "trust": str(s.fact.trust),
                "home_region": s.fact.home_region,
                "confidence": s.fact.confidence,
                "corroboration_count": s.fact.corroboration_count,
                "score": round(s.score, 6),
                "score_breakdown": {
                    "similarity": round(s.breakdown.similarity, 6),
                    "strength": s.breakdown.strength,
                    "confidence": s.breakdown.confidence,
                    "trust_weight": s.breakdown.trust_weight,
                },
                # Which episodes this fact traces back to — invariant 3, made
                # inspectable. Event content is never included here, only the
                # id and a hash (explain() is the same: it never decrypts or
                # returns episode text at all, by design, which is a stronger
                # guarantee than residency-filtering would be, not a
                # substitute for it).
                "provenance": [
                    {"event_id": str(p.event_id), "weight": p.weight} for p in s.provenance
                ],
            }
            for s in result.facts
        ],
        "contested": [
            {
                "left": {"fact_id": str(p.left.fact_id), "text": p.left.text},
                "right": {"fact_id": str(p.right.fact_id), "text": p.right.text},
                "note": "both sides returned; the system has not picked a winner",
            }
            for p in result.contested
        ],
        "unverified_withheld": result.unverified_withheld,
        "recall_ids": [str(r) for r in result.recall_ids],
    }


__all__ = ["MnemosError", "ToolError", "register_tools"]
