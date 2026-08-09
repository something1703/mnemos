"""The Warden — the single entry point for every governance operation.

Every method here requires `confirm=True` and a non-empty `reason`. Neither is
a formality:

  * `confirm` exists so a destructive call can never be a one-argument
    accident — no code path in this system can `forget()` a subject by
    calling it the way you would call `recall()`.
  * `reason` is stored on the audit row. An erasure with no stated reason is
    unauditable even if it is otherwise perfectly logged.

Dual control — a tenant-configurable requirement for two distinct admin keys
on destructive operations — is checked here too, when a caller passes
`admin_key_id`/`admin_label` (the API layer's job is resolving those from a
presented key and deciding whether to pass them at all; this class does not
authenticate anything). `admin_key_id=None` (the default) skips the check
entirely, which is what every non-API caller — tests, demos, the CLI — wants:
dual control is a property of agent-facing admin traffic, not of this class's
own contract. See `approvals.py` for why the DELETE this needs lives here
rather than in `services/api`.

This class deliberately has ZERO dependency on any LLM SDK — see
`guarantees.py` and `make no-model-in-warden`. It is the only thing in Mnemos
that may destroy anything (invariant 1).
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

import psycopg
from mnemos_engine.db import Database
from mnemos_engine.integrity import BlastRadius

from . import erasure
from .approvals import enforce as _enforce_dual_control
from .errors import ConfirmationRequired
from .holds import active_hold, list_holds, release_legal_hold, set_legal_hold
from .keys import KeyProvider
from .models import EraseMode, ErasurePreview, ErasureResult, LegalHold, RevocationRecord
from .residency import ResidencyReport
from .residency import where_is as _where_is
from .revoke import preview_revocation
from .revoke import revoke_source as _revoke_source

log = logging.getLogger("mnemos.warden")


def _require(confirm: bool, reason: str, operation: str) -> None:
    if not confirm:
        raise ConfirmationRequired(
            f"{operation} requires confirm=True — this is irreversible or "
            "governance-affecting and cannot be triggered by an ordinary call"
        )
    if not reason or not reason.strip():
        raise ConfirmationRequired(f"{operation} requires a non-empty reason")


async def _maybe_dual_control(
    db: Database,
    tenant_id: UUID,
    *,
    operation: str,
    target_key: str,
    reason: str,
    admin_key_id: UUID | None,
    admin_label: str | None,
) -> None:
    """No-op when `admin_key_id` is absent — every non-API caller (tests,
    demos, the CLI) gets exactly the old, ungated behaviour. See
    `approvals.py` for what happens when it is present."""
    if admin_key_id is None:
        return
    assert admin_label is not None, "admin_label is required whenever admin_key_id is given"
    await _enforce_dual_control(
        db,
        tenant_id,
        operation=operation,
        target_key=target_key,
        reason=reason,
        admin_key_id=admin_key_id,
        admin_label=admin_label,
    )


class Warden:
    def __init__(self, db: Database, *, key_provider: KeyProvider) -> None:
        self._db = db
        self._keys = key_provider

    # ------------------------------------------------------------ erasure

    async def preview_erasure(
        self, tenant_id: UUID, subject_key: str, mode: EraseMode
    ) -> ErasurePreview:
        """No confirm/reason gate — a preview destroys nothing and is what a
        console renders before the confirm button exists on screen."""

        async def run(cur: psycopg.AsyncCursor) -> ErasurePreview:
            return await erasure.preview(cur, tenant_id, subject_key, mode)

        return await self._db.transaction(tenant_id, run, label="preview_erasure")

    async def redact(
        self,
        tenant_id: UUID,
        subject_key: str,
        *,
        actor: str,
        reason: str,
        confirm: bool = False,
        admin_key_id: UUID | None = None,
        admin_label: str | None = None,
    ) -> ErasureResult:
        _require(confirm, reason, "redact")
        await _maybe_dual_control(
            self._db,
            tenant_id,
            operation="redact",
            target_key=subject_key,
            reason=reason,
            admin_key_id=admin_key_id,
            admin_label=admin_label,
        )

        async def run(cur: psycopg.AsyncCursor) -> ErasureResult:
            return await erasure.execute_redact(
                cur, tenant_id, subject_key, actor=actor, reason=reason
            )

        result = await self._db.transaction(tenant_id, run, label="redact")
        log.warning("redact executed", extra={"subject_key": subject_key, "actor": actor})
        return result

    async def forget(
        self,
        tenant_id: UUID,
        subject_key: str,
        *,
        actor: str,
        reason: str,
        confirm: bool = False,
        admin_key_id: UUID | None = None,
        admin_label: str | None = None,
    ) -> ErasureResult:
        _require(confirm, reason, "forget")
        await _maybe_dual_control(
            self._db,
            tenant_id,
            operation="forget",
            target_key=subject_key,
            reason=reason,
            admin_key_id=admin_key_id,
            admin_label=admin_label,
        )

        async def run(cur: psycopg.AsyncCursor) -> ErasureResult:
            return await erasure.execute_forget(
                cur, tenant_id, subject_key, actor=actor, reason=reason
            )

        result = await self._db.transaction(tenant_id, run, label="forget")
        log.warning(
            "forget executed",
            extra={
                "subject_key": subject_key,
                "actor": actor,
                "episodes": result.episodes_removed,
                "facts": result.facts_removed,
            },
        )
        return result

    async def quarantine(
        self,
        tenant_id: UUID,
        subject_key: str,
        *,
        actor: str,
        reason: str,
        confirm: bool = False,
        admin_key_id: UUID | None = None,
        admin_label: str | None = None,
    ) -> ErasureResult:
        _require(confirm, reason, "quarantine")
        await _maybe_dual_control(
            self._db,
            tenant_id,
            operation="quarantine",
            target_key=subject_key,
            reason=reason,
            admin_key_id=admin_key_id,
            admin_label=admin_label,
        )

        async def run(cur: psycopg.AsyncCursor) -> ErasureResult:
            return await erasure.execute_quarantine(
                cur, tenant_id, subject_key, actor=actor, reason=reason
            )

        return await self._db.transaction(tenant_id, run, label="quarantine")

    async def shred(
        self,
        tenant_id: UUID,
        subject_key: str,
        *,
        actor: str,
        reason: str,
        confirm: bool = False,
        admin_key_id: UUID | None = None,
        admin_label: str | None = None,
    ) -> ErasureResult:
        """FORGET plus destruction of the tenant's data key. Irreversible and
        TENANT-WIDE: every row this tenant has ever written becomes
        unrecoverable, not just this subject's. Confirm here is confirming
        that scope, not just this one subject's erasure."""
        _require(confirm, reason, "shred")
        await _maybe_dual_control(
            self._db,
            tenant_id,
            operation="shred",
            target_key=subject_key,
            reason=reason,
            admin_key_id=admin_key_id,
            admin_label=admin_label,
        )

        async def run(cur: psycopg.AsyncCursor) -> ErasureResult:
            return await erasure.execute_shred(
                cur, tenant_id, subject_key, actor=actor, reason=reason, key_provider=self._keys
            )

        result = await self._db.transaction(tenant_id, run, label="shred")
        log.warning(
            "SHRED executed — tenant key destroyed",
            extra={"tenant_id": str(tenant_id), "subject_key": subject_key, "actor": actor},
        )
        return result

    # ------------------------------------------------------------- revoke

    async def preview_revoke_source(
        self, tenant_id: UUID, source_event_ids: list[UUID]
    ) -> BlastRadius:
        async def run(cur: psycopg.AsyncCursor) -> BlastRadius:
            return await preview_revocation(cur, tenant_id, source_event_ids)

        return await self._db.transaction(tenant_id, run, label="preview_revoke")

    async def revoke_source(
        self,
        tenant_id: UUID,
        source_event_ids: list[UUID],
        *,
        actor: str,
        reason: str,
        confirm: bool = False,
        admin_key_id: UUID | None = None,
        admin_label: str | None = None,
    ) -> RevocationRecord:
        _require(confirm, reason, "revoke_source")
        await _maybe_dual_control(
            self._db,
            tenant_id,
            operation="revoke_source",
            target_key=",".join(sorted(str(e) for e in source_event_ids)),
            reason=reason,
            admin_key_id=admin_key_id,
            admin_label=admin_label,
        )

        async def run(cur: psycopg.AsyncCursor) -> RevocationRecord:
            return await _revoke_source(
                cur, tenant_id, source_event_ids, actor=actor, reason=reason
            )

        record = await self._db.transaction(tenant_id, run, label="revoke_source")
        counts = record.radius_manifest.get("counts")
        facts_touched = counts.get("facts") if isinstance(counts, dict) else None
        log.warning(
            "source revoked",
            extra={
                "tenant_id": str(tenant_id),
                "revocation_id": str(record.revocation_id),
                "facts_touched": facts_touched,
            },
        )
        return record

    # ------------------------------------------------------------- holds

    async def set_legal_hold(
        self,
        tenant_id: UUID,
        subject_key: str,
        *,
        matter_reference: str,
        placed_by: str,
        expires_at: datetime | None = None,
        confirm: bool = False,
        admin_key_id: UUID | None = None,
        admin_label: str | None = None,
    ) -> LegalHold:
        _require(confirm, matter_reference, "set_legal_hold")
        await _maybe_dual_control(
            self._db,
            tenant_id,
            operation="set_legal_hold",
            target_key=subject_key,
            reason=matter_reference,
            admin_key_id=admin_key_id,
            admin_label=admin_label,
        )

        async def run(cur: psycopg.AsyncCursor) -> LegalHold:
            return await set_legal_hold(
                cur,
                tenant_id,
                subject_key=subject_key,
                matter_reference=matter_reference,
                placed_by=placed_by,
                expires_at=expires_at,
            )

        return await self._db.transaction(tenant_id, run, label="set_legal_hold")

    async def release_legal_hold(
        self,
        tenant_id: UUID,
        hold_id: UUID,
        *,
        released_by: str,
        release_reason: str,
        confirm: bool = False,
    ) -> LegalHold | None:
        _require(confirm, release_reason, "release_legal_hold")

        async def run(cur: psycopg.AsyncCursor) -> LegalHold | None:
            return await release_legal_hold(
                cur, tenant_id, hold_id, released_by=released_by, release_reason=release_reason
            )

        return await self._db.transaction(tenant_id, run, label="release_legal_hold")

    async def check_hold(self, tenant_id: UUID, subject_key: str) -> LegalHold | None:
        async def run(cur: psycopg.AsyncCursor) -> LegalHold | None:
            return await active_hold(cur, tenant_id, subject_key)

        return await self._db.transaction(tenant_id, run, label="check_hold", read_only=True)

    async def list_holds(self, tenant_id: UUID, *, active_only: bool = True) -> list[LegalHold]:
        async def run(cur: psycopg.AsyncCursor) -> list[LegalHold]:
            return await list_holds(cur, tenant_id, active_only=active_only)

        return await self._db.transaction(tenant_id, run, label="list_holds", read_only=True)

    # ---------------------------------------------------------- residency

    async def where_is(self, tenant_id: UUID, subject_key: str) -> ResidencyReport:
        async def run(cur: psycopg.AsyncCursor) -> ResidencyReport:
            return await _where_is(cur, tenant_id, subject_key)

        return await self._db.transaction(tenant_id, run, label="where_is", read_only=True)
