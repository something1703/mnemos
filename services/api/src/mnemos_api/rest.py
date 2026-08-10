"""REST facade — what the console reads and what a judge can curl.

Two deliberate departures from the PHASE_04.3 sketch, both toward safety:

* **No `{tenant}` path parameter.** The sketch had `/ledger/{tenant}`. Taking
  a tenant from the URL invites the bug where a caller passes someone else's
  and the handler forgets to check. The tenant is derived from the API key and
  cannot be overridden, so cross-tenant access is not a check that can be
  forgotten — it is unrepresentable. Switching tenants means using that
  tenant's key, which is what multi-tenancy should mean anyway.

* **No SSE stream yet.** The sketch wired `/events/stream` to a CHANGEFEED.
  That is real work with real failure modes, and shipping a fake version that
  polls behind an SSE-shaped URL would be worse than not shipping it — the
  console would be built against a contract we had not actually honoured. It
  lands with Phase 08, where something consumes it.

Everything here is read-only. Mutation goes through MCP, where the scope gate
and confirm semantics live; a REST endpoint that could delete would be a
second, weaker door onto the same room.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from mnemos_engine.accountability import build_verifiable_export
from mnemos_engine.accountability import explain as engine_explain
from mnemos_engine.ledger import verify_chain
from mnemos_warden.attestation import presign_anchor_url

from .deposition_html import render_deposition_html
from .keys import AuthError, Principal, Scope, require, resolve_key
from .runtime import Runtime

log = logging.getLogger("mnemos.api.rest")


async def _principal_from_request(request: Request) -> Principal:
    runtime: Runtime = request.app.state.runtime
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(401, "missing Authorization: Bearer mn_live_...")
    try:
        return await runtime.db.transaction(
            None, lambda cur: resolve_key(cur, token), label="resolve_key"
        )
    except AuthError as exc:
        log.warning("rest auth rejected path=%s reason=%s", request.url.path, exc)
        raise HTTPException(exc.status, str(exc)) from exc


CurrentPrincipal = Annotated[Principal, Depends(_principal_from_request)]


def build_rest_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(
        title="Mnemos REST",
        version="0.1.0",
        description=(
            "Read-only views over accountable agent memory. Every response is "
            "scoped to the tenant that owns the presented API key; the tenant is "
            "never taken from the URL. Mutation is MCP-only."
        ),
    )
    app.state.runtime = runtime

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, Any]:
        """Unauthenticated liveness + the instance's actual security posture.

        The posture is public on purpose: a deployment that has silently lost
        privilege separation should be visible without needing a credential to
        find out.
        """
        try:
            await runtime.db.transaction(None, lambda cur: cur.execute("SELECT 1"), label="health")
            database = "ok"
        except Exception as exc:
            log.error("health: database unreachable: %s", exc)
            database = "unreachable"

        return {
            "status": "ok" if database == "ok" else "degraded",
            "database": database,
            "posture": runtime.describe_posture(),
        }

    @app.get("/v1/stats", tags=["memory"])
    async def stats(principal: CurrentPrincipal) -> dict[str, Any]:
        require(principal, Scope.READ, "stats")

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
            by_trust = {str(r[0]): int(r[1]) for r in await cur.fetchall()}
            await cur.execute(
                "SELECT count(*) FROM mnemos.audit_chain WHERE tenant_id = %s",
                (principal.tenant_id,),
            )
            chain = int((await cur.fetchone())[0])
            await cur.execute(
                "SELECT count(*) FROM mnemos.legal_holds "
                "WHERE tenant_id = %s AND released_at IS NULL",
                (principal.tenant_id,),
            )
            holds = int((await cur.fetchone())[0])
            return {
                "episodes": episodes,
                "facts_by_trust": by_trust,
                "chain_entries": chain,
                "active_holds": holds,
            }

        result = await runtime.db.transaction(
            principal.tenant_id, run, label="rest_stats", read_only=True
        )
        result["posture"] = runtime.describe_posture()
        return result

    @app.get("/v1/facts", tags=["memory"])
    async def facts(
        principal: CurrentPrincipal,
        trust: Annotated[str | None, Query(description="filter by trust state")] = None,
        subject: Annotated[str | None, Query(description="exact subject_key")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Browse facts. Text is NOT decrypted here.

        The console shows metadata, trust state and provenance counts; reading
        content is a recall, which is logged. Letting a browse endpoint quietly
        return plaintext would create an unlogged read path around exactly the
        accountability this system exists to provide.
        """
        require(principal, Scope.READ, "facts")

        clauses = ["tenant_id = %s"]
        params: list[Any] = [principal.tenant_id]
        if trust:
            clauses.append("trust = %s")
            params.append(trust)
        if subject:
            clauses.append("subject_key = %s")
            params.append(subject)
        where = " AND ".join(clauses)

        async def run(cur: Any) -> dict[str, Any]:
            await cur.execute(
                f"SELECT count(*) FROM mnemos.semantic_facts WHERE {where}",  # noqa: S608
                tuple(params),
            )
            total = int((await cur.fetchone())[0])
            await cur.execute(
                f"""
                SELECT f.fact_id, f.subject_key, f.fact_kind, f.trust, f.home_region,
                       f.strength, f.confidence, f.corroboration_count, f.recall_count,
                       f.created_at, f.superseded_by, f.contested_with, f.revoked_at,
                       (SELECT count(*) FROM mnemos.fact_provenance p
                        WHERE p.tenant_id = f.tenant_id AND p.fact_id = f.fact_id)
                FROM mnemos.semantic_facts f WHERE {where}
                ORDER BY f.created_at DESC LIMIT %s OFFSET %s
                """,  # noqa: S608
                (*params, limit, offset),
            )
            rows = await cur.fetchall()
            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "facts": [
                    {
                        "fact_id": str(r[0]),
                        "subject_key": r[1],
                        "fact_kind": r[2],
                        "trust": str(r[3]),
                        "home_region": r[4],
                        "strength": float(r[5]),
                        "confidence": float(r[6]),
                        "corroboration_count": int(r[7]),
                        "recall_count": int(r[8]),
                        "created_at": r[9].isoformat(),
                        "superseded": r[10] is not None,
                        "contested": r[11] is not None,
                        "revoked": r[12] is not None,
                        "provenance_edges": int(r[13]),
                    }
                    for r in rows
                ],
            }

        return await runtime.db.transaction(
            principal.tenant_id, run, label="rest_facts", read_only=True
        )

    @app.get("/v1/ledger", tags=["ledger"])
    async def ledger(
        principal: CurrentPrincipal,
        shard: Annotated[int | None, Query(ge=0, le=255)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """The audit chain, newest first. Hashes are hex for display."""
        require(principal, Scope.READ, "ledger")

        clauses = ["tenant_id = %s"]
        params: list[Any] = [principal.tenant_id]
        if shard is not None:
            clauses.append("shard_id = %s")
            params.append(shard)
        where = " AND ".join(clauses)

        async def run(cur: Any) -> dict[str, Any]:
            await cur.execute(
                f"SELECT count(*) FROM mnemos.audit_chain WHERE {where}",  # noqa: S608
                tuple(params),
            )
            total = int((await cur.fetchone())[0])
            await cur.execute(
                f"""
                SELECT shard_id, seq, op, actor, subject_key, reason,
                       entry_hash, committed_at
                FROM mnemos.audit_chain WHERE {where}
                ORDER BY committed_at DESC LIMIT %s OFFSET %s
                """,  # noqa: S608
                (*params, limit, offset),
            )
            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "entries": [
                    {
                        "shard_id": int(r[0]),
                        "seq": int(r[1]),
                        "op": str(r[2]),
                        "actor": r[3],
                        "subject_key": r[4],
                        "reason": r[5],
                        "entry_hash": bytes(r[6]).hex(),
                        "committed_at": r[7].isoformat(),
                    }
                    for r in await cur.fetchall()
                ],
            }

        return await runtime.db.transaction(
            principal.tenant_id, run, label="rest_ledger", read_only=True
        )

    @app.get("/v1/ledger/verify", tags=["ledger"])
    async def ledger_verify(principal: CurrentPrincipal) -> dict[str, Any]:
        require(principal, Scope.READ, "ledger/verify")

        async def run(cur: Any) -> Any:
            return await verify_chain(cur, principal.tenant_id)

        result = await runtime.db.transaction(
            principal.tenant_id, run, label="rest_verify", read_only=True
        )
        return {
            "valid": result.valid,
            "entries_checked": result.entries_checked,
            "shards_checked": result.shards_checked,
            "checkpoints_checked": result.checkpoints_checked,
            "broken_at": list(result.broken_at) if result.broken_at else None,
            "detail": result.detail,
            # An unanchored chain is weaker than a bare VALID implies, and the
            # console must be able to render that distinction.
            "caveat": (
                "no checkpoints exist for this tenant, so a consistent whole-shard "
                "rewrite would not be detectable from inside the database"
                if result.checkpoints_checked == 0
                else None
            ),
        }

    @app.get("/v1/checkpoints", tags=["ledger"])
    async def checkpoints(principal: CurrentPrincipal) -> dict[str, Any]:
        require(principal, Scope.READ, "checkpoints")

        async def run(cur: Any) -> list[dict[str, Any]]:
            await cur.execute(
                "SELECT checkpoint_seq, merkle_root, entry_count, covers_through, "
                "       anchor_uri, anchored_at "
                "FROM mnemos.chain_checkpoints WHERE tenant_id = %s "
                "ORDER BY checkpoint_seq DESC",
                (principal.tenant_id,),
            )
            return [
                {
                    "checkpoint_seq": int(r[0]),
                    "merkle_root": bytes(r[1]).hex(),
                    "entry_count": int(r[2]),
                    "covers_through": r[3].isoformat(),
                    "anchor_uri": r[4],
                    # An unanchored checkpoint is not yet evidence of anything.
                    "anchored": r[5] is not None,
                    "anchored_at": r[5].isoformat() if r[5] else None,
                }
                for r in await cur.fetchall()
            ]

        return {
            "checkpoints": await runtime.db.transaction(
                principal.tenant_id, run, label="rest_checkpoints", read_only=True
            )
        }

    @app.get("/v1/deposition/{action_id}", tags=["accountability"])
    async def deposition(action_id: UUID, principal: CurrentPrincipal) -> dict[str, Any]:
        require(principal, Scope.READ, "deposition")

        async def run(cur: Any) -> Any:
            return await engine_explain(cur, principal.tenant_id, action_id)

        result = await runtime.db.transaction(
            principal.tenant_id, run, label="rest_deposition", read_only=True
        )
        if result is None:
            raise HTTPException(404, f"no action {action_id} in this tenant")

        return {
            "action_id": str(result.action_id),
            "action_type": result.action_type,
            "description": result.description,
            "declared_at": result.declared_at.isoformat(),
            "contaminated": result.contaminated,
            "contamination_note": result.contamination_note,
            "checkpoint_seq": result.checkpoint_seq,
            "merkle_root": result.merkle_root,
            "anchor_uri": result.anchor_uri,
            "anchored": result.anchor_uri is not None,
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
                            "home_region": e.home_region,
                            "occurred_at": e.occurred_at.isoformat(),
                        }
                        for e in f.provenance
                    ],
                }
                for f in result.facts
            ],
            "audit_trail": [
                {
                    "shard_id": a.shard_id,
                    "seq": a.seq,
                    "op": a.op,
                    "actor": a.actor,
                    "entry_hash": a.entry_hash,
                    "committed_at": a.committed_at.isoformat(),
                }
                for a in result.audit_trail
            ],
            "summary": result.summary(),
        }

    @app.get("/v1/deposition/{action_id}/export.html", tags=["accountability"])
    async def deposition_export(action_id: UUID, principal: CurrentPrincipal) -> HTMLResponse:
        """The artifact PHASE_06 6.7 calls the clearest single expression of
        what Mnemos is for: a self-contained HTML file that renders this
        deposition and independently reverifies its own hashes offline, in
        the browser — the thing you hand an auditor or attach to an incident
        report. See `mnemos_api.deposition_html` for what it actually proves
        and what it cannot (verifying against the outside S3 anchor needs a
        network call, offered as an explicit, separate button)."""
        require(principal, Scope.READ, "deposition_export")

        async def run(cur: Any) -> Any:
            return await build_verifiable_export(cur, principal.tenant_id, action_id)

        bundle = await runtime.db.transaction(
            principal.tenant_id, run, label="rest_deposition_export", read_only=True
        )
        if bundle is None:
            raise HTTPException(404, f"no action {action_id} in this tenant")

        bundle["deposition"] = bundle["deposition"].model_dump(mode="json")

        anchor_url: str | None = None
        checkpoint = bundle["checkpoint"]
        if (
            checkpoint
            and checkpoint.get("anchor_uri")
            and runtime.s3
            and runtime.settings.anchor_bucket
        ):
            try:
                anchor_url = presign_anchor_url(
                    s3=runtime.s3,
                    bucket=runtime.settings.anchor_bucket,
                    tenant_id=principal.tenant_id,
                    checkpoint_seq=checkpoint["checkpoint_seq"],
                )
            except Exception:
                log.warning("failed to presign anchor URL for deposition export", exc_info=True)

        return HTMLResponse(content=render_deposition_html(bundle, anchor_presigned_url=anchor_url))

    @app.get("/v1/residency/{subject_key:path}", tags=["governance"])
    async def residency(subject_key: str, principal: CurrentPrincipal) -> dict[str, Any]:
        require(principal, Scope.READ, "residency")
        report = await runtime.warden.where_is(principal.tenant_id, subject_key)
        return {
            "subject_key": report.subject_key,
            "episode_regions": report.episode_regions,
            "fact_regions": report.fact_regions,
            "governing_policy": report.governing_policy,
        }

    @app.get("/v1/holds", tags=["governance"])
    async def holds(
        principal: CurrentPrincipal,
        active_only: bool = True,
    ) -> dict[str, Any]:
        require(principal, Scope.READ, "holds")
        found = await runtime.warden.list_holds(principal.tenant_id, active_only=active_only)
        return {
            "holds": [
                {
                    "hold_id": str(h.hold_id),
                    "subject_key": h.subject_key,
                    "matter_reference": h.matter_reference,
                    "placed_by": h.placed_by,
                    "placed_at": h.placed_at.isoformat(),
                    "released_at": h.released_at.isoformat() if h.released_at else None,
                    "active": h.is_active,
                }
                for h in found
            ]
        }

    @app.get("/v1/crossings", tags=["governance"])
    async def crossings(
        principal: CurrentPrincipal,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        """Border crossings, permitted AND refused.

        Denials are included deliberately: a residency log that recorded only
        its successes could not answer the question an auditor actually asks,
        which is what you stopped.
        """
        require(principal, Scope.READ, "crossings")

        async def run(cur: Any) -> list[dict[str, Any]]:
            await cur.execute(
                "SELECT subject_key, from_region, to_region, projection, "
                "       policy_applied, allowed, denied_reason, requested_by, occurred_at "
                "FROM mnemos.region_crossings WHERE tenant_id = %s "
                "ORDER BY occurred_at DESC LIMIT %s",
                (principal.tenant_id, limit),
            )
            return [
                {
                    "subject_key": r[0],
                    "from_region": r[1],
                    "to_region": r[2],
                    "projection": r[3],
                    "policy_applied": r[4],
                    "allowed": bool(r[5]),
                    "denied_reason": r[6],
                    "requested_by": r[7],
                    "occurred_at": r[8].isoformat(),
                }
                for r in await cur.fetchall()
            ]

        return {
            "crossings": await runtime.db.transaction(
                principal.tenant_id, run, label="rest_crossings", read_only=True
            )
        }

    @app.get("/v1/custodian/runs", tags=["custodian"])
    async def custodian_runs(
        principal: CurrentPrincipal,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """Sweep history, including what each run could NOT check.

        `checks_skipped` and `skipped_detail` are returned alongside the
        successes for the same reason the crossings log includes denials:
        a coverage report that lists only what it managed to look at reads as
        full coverage, which is exactly the failure mode PHASE_07 7.1 calls
        out.
        """
        require(principal, Scope.READ, "custodian_runs")

        async def run(cur: Any) -> list[dict[str, Any]]:
            await cur.execute(
                "SELECT run_id, trigger_source, trigger_detail, started_at, finished_at, "
                "       status, skills_run, checks_run, checks_skipped, skipped_detail "
                "FROM mnemos.custodian_runs WHERE tenant_id = %s "
                "ORDER BY started_at DESC LIMIT %s",
                (principal.tenant_id, limit),
            )
            return [
                {
                    "run_id": str(r[0]),
                    "trigger_source": r[1],
                    "trigger_detail": r[2],
                    "started_at": r[3].isoformat(),
                    "finished_at": r[4].isoformat() if r[4] else None,
                    "status": r[5],
                    "skills_run": r[6],
                    "checks_run": r[7],
                    "checks_skipped": r[8],
                    "skipped_detail": r[9],
                }
                for r in await cur.fetchall()
            ]

        return {
            "runs": await runtime.db.transaction(
                principal.tenant_id, run, label="rest_custodian_runs", read_only=True
            )
        }

    @app.get("/v1/custodian/findings", tags=["custodian"])
    async def custodian_findings(
        principal: CurrentPrincipal,
        run_id: UUID | None = None,
        severity: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        """Findings, carrying both provenance axes the console needs.

        `tool_source` says which CockroachDB surface produced the data (the
        Cloud MCP Server or the control-plane API), and `measured` says
        whether the finding is a deterministic reading or the interpreter's
        opinion. Those are different questions, and collapsing them would
        hide the distinction that makes a Custodian finding corroborable at
        all — see mnemos_custodian.sweep.
        """
        require(principal, Scope.READ, "custodian_findings")

        async def run(cur: Any) -> list[dict[str, Any]]:
            # Static SQL with nullable predicates rather than a WHERE clause
            # assembled from strings. The assembled version is equivalent here
            # (the fragments are literals; only values are bound) and is what
            # /v1/facts above still does — but ADR-011's whole position is that
            # the injection-shaped SURFACE is the thing worth removing, not
            # just the injection. A query with no interpolation needs no
            # reader to verify that claim, and no suppression comment to
            # silence the linter that would otherwise keep asking.
            await cur.execute(
                "SELECT finding_id, run_id, severity, summary, evidence, recommendation, "
                "       skill_id, tool_source, code, measured, fact_id, created_at "
                "FROM mnemos.custodian_findings "
                "WHERE tenant_id = %(tenant)s "
                "  AND (%(run_id)s::UUID IS NULL OR run_id = %(run_id)s) "
                "  AND (%(severity)s::STRING IS NULL OR severity = %(severity)s) "
                "ORDER BY created_at DESC LIMIT %(limit)s",
                {
                    "tenant": principal.tenant_id,
                    "run_id": run_id,
                    "severity": severity,
                    "limit": limit,
                },
            )
            return [
                {
                    "finding_id": str(r[0]),
                    "run_id": str(r[1]),
                    "severity": r[2],
                    "summary": r[3],
                    "evidence": r[4],
                    "recommendation": r[5],
                    "skill_id": r[6],
                    "tool_source": r[7],
                    "code": r[8],
                    "measured": bool(r[9]),
                    "fact_id": str(r[10]) if r[10] else None,
                    "created_at": r[11].isoformat(),
                }
                for r in await cur.fetchall()
            ]

        return {
            "findings": await runtime.db.transaction(
                principal.tenant_id, run, label="rest_custodian_findings", read_only=True
            )
        }

    @app.get("/v1/governance/proposals", tags=["governance"])
    async def governance_proposals(
        principal: CurrentPrincipal,
        status: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        """What the Custodian has asked a human to decide.

        Read-only here on purpose. Approving a proposal is a Warden operation
        behind an admin scope and dual control — it is deliberately not
        reachable by adding a verb to this endpoint.
        """
        require(principal, Scope.READ, "governance_proposals")

        async def run(cur: Any) -> list[dict[str, Any]]:
            await cur.execute(
                "SELECT proposal_id, proposed_by, kind, target, rationale, evidence, "
                "       status, decided_by, decided_at, decision_note, created_at "
                "FROM mnemos.governance_proposals "
                "WHERE tenant_id = %(tenant)s "
                "  AND (%(status)s::STRING IS NULL OR status = %(status)s) "
                "ORDER BY created_at DESC LIMIT %(limit)s",
                {"tenant": principal.tenant_id, "status": status, "limit": limit},
            )
            return [
                {
                    "proposal_id": str(r[0]),
                    "proposed_by": r[1],
                    "kind": r[2],
                    "target": r[3],
                    "rationale": r[4],
                    "evidence": r[5],
                    "status": r[6],
                    "decided_by": r[7],
                    "decided_at": r[8].isoformat() if r[8] else None,
                    "decision_note": r[9],
                    "created_at": r[10].isoformat(),
                }
                for r in await cur.fetchall()
            ]

        return {
            "proposals": await runtime.db.transaction(
                principal.tenant_id, run, label="rest_proposals", read_only=True
            )
        }

    return app
