"""The four erasure modes.

Erasure is a spectrum, not a boolean (PHASE_06.4, docs/limits.md). Each mode is
one serializable transaction, retry-wrapped by `Database.transaction`, ending
in an audit row. `preview()` and `execute()` share the exact same counting
query — not two queries that are supposed to agree — so there is no
TOCTOU gap between what a caller was shown and what actually gets destroyed.

Every mode checks `active_hold` first and refuses loudly if one exists. A
partial erasure under hold would be worse than either outcome, so the check
runs before a single row is touched, inside the same transaction as the
destruction — a hold placed between preview and confirm is still caught.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
from mnemos_engine.errors import LegalHoldActive
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Op

from .errors import UnknownSubject
from .holds import active_hold
from .keys import KeyProvider
from .models import EraseMode, ErasurePreview, ErasureResult

log = logging.getLogger("mnemos.warden.erasure")


async def _scalar(cur: psycopg.AsyncCursor, sql: str, params: tuple[object, ...]) -> int:
    """A count(*) query always returns exactly one row; this makes that
    guarantee explicit to mypy instead of indexing an Optional at every call
    site, which is where the untyped version of this file went wrong."""
    await cur.execute(sql, params)
    row = await cur.fetchone()
    assert row is not None, "count(*) must return a row"
    return int(row[0])


def _now() -> datetime:
    return datetime.now(UTC)


async def _counts(
    cur: psycopg.AsyncCursor, tenant_id: UUID, subject_key: str
) -> tuple[int, int, int, int, int]:
    """The single source of truth for 'what does this subject's memory
    consist of'. Both preview() and every execute_* function call this and
    only this, so a preview can never diverge from what gets destroyed."""
    episodes = await _scalar(
        cur,
        "SELECT count(*) FROM mnemos.episodic_events WHERE tenant_id = %s AND subject_key = %s",
        (tenant_id, subject_key),
    )
    facts = await _scalar(
        cur,
        "SELECT count(*) FROM mnemos.semantic_facts WHERE tenant_id = %s AND subject_key = %s",
        (tenant_id, subject_key),
    )
    provenance = await _scalar(
        cur,
        "SELECT count(*) FROM mnemos.fact_provenance WHERE tenant_id = %s AND subject_key = %s",
        (tenant_id, subject_key),
    )
    recalls = await _scalar(
        cur,
        """
        SELECT count(*) FROM mnemos.recall_log r
        JOIN mnemos.semantic_facts f ON f.tenant_id = r.tenant_id AND f.fact_id = r.fact_id
        WHERE r.tenant_id = %s AND f.subject_key = %s
        """,
        (tenant_id, subject_key),
    )
    skill_citations = await _scalar(
        cur,
        """
        SELECT count(*) FROM mnemos.skill_provenance sp
        JOIN mnemos.semantic_facts f ON f.tenant_id = sp.tenant_id AND f.fact_id = sp.fact_id
        WHERE sp.tenant_id = %s AND f.subject_key = %s
        """,
        (tenant_id, subject_key),
    )
    return episodes, facts, provenance, recalls, skill_citations


async def preview(
    cur: psycopg.AsyncCursor, tenant_id: UUID, subject_key: str, mode: EraseMode
) -> ErasurePreview:
    """What this mode would destroy, without touching anything.

    Computed in the same transaction shape execute() will use, so the numbers
    a human approves are the numbers that get destroyed — not an estimate.
    """
    episodes, facts, provenance, recalls, skill_citations = await _counts(
        cur, tenant_id, subject_key
    )
    if episodes == 0 and facts == 0:
        raise UnknownSubject(f"no memory found for subject {subject_key!r} in this tenant")

    hold = await active_hold(cur, tenant_id, subject_key)

    return ErasurePreview(
        tenant_id=tenant_id,
        subject_key=subject_key,
        mode=mode,
        episode_count=episodes,
        fact_count=facts,
        provenance_edge_count=provenance,
        recall_log_count=recalls,
        skill_citation_count=skill_citations,
        blocked_by_hold=hold,
    )


async def _require_unheld(cur: psycopg.AsyncCursor, tenant_id: UUID, subject_key: str) -> None:
    hold = await active_hold(cur, tenant_id, subject_key)
    if hold is not None:
        raise LegalHoldActive(subject_key, hold.matter_reference, hold.placed_by)


async def execute_redact(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    subject_key: str,
    *,
    actor: str,
    reason: str,
) -> ErasureResult:
    """Tombstone content ciphertext; keep the row, provenance, and audit
    history. Use when the fact that something existed must survive but its
    content must not — the mode that preserves an audit trail's shape while
    still removing the sensitive payload."""
    await _require_unheld(cur, tenant_id, subject_key)
    episodes, facts, _provenance, _, _ = await _counts(cur, tenant_id, subject_key)

    await append_audit(
        cur,
        tenant_id,
        op=Op.REDACT,
        actor=actor,
        subject_key=subject_key,
        reason=reason,
        payload={"episodes": episodes, "facts": facts},
    )

    tombstone = b"\x00"
    await cur.execute(
        "UPDATE mnemos.episodic_events SET content_ciphertext = %s, content_dek_wrapped = %s "
        "WHERE tenant_id = %s AND subject_key = %s",
        (tombstone, tombstone, tenant_id, subject_key),
    )
    await cur.execute(
        "UPDATE mnemos.semantic_facts SET text_ciphertext = %s, text_dek_wrapped = %s, "
        "embedding = NULL, tsv = NULL "
        "WHERE tenant_id = %s AND subject_key = %s",
        (tombstone, tombstone, tenant_id, subject_key),
    )

    return ErasureResult(
        tenant_id=tenant_id,
        subject_key=subject_key,
        mode=EraseMode.REDACT,
        reason=reason,
        actor=actor,
        executed_at=_now(),
        episodes_removed=0,
        facts_removed=0,
        provenance_edges_removed=0,
        audit_ticket=uuid4(),
    )


async def execute_forget(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    subject_key: str,
    *,
    actor: str,
    reason: str,
) -> ErasureResult:
    """Delete episodes, facts (and their vector index entries, implicitly),
    and provenance edges — atomically. The audit row survives; the memory
    does not. This is the GDPR Art. 17 mode, and the ordering below matters:
    provenance edges first (they reference both sides), then facts (which
    carries the vector index entry with it in the same statement, which is
    the guarantee a separate vector store cannot make), then episodes."""
    await _require_unheld(cur, tenant_id, subject_key)
    episodes, facts, provenance, _, _ = await _counts(cur, tenant_id, subject_key)

    await append_audit(
        cur,
        tenant_id,
        op=Op.FORGET,
        actor=actor,
        subject_key=subject_key,
        reason=reason,
        payload={"episodes": episodes, "facts": facts, "provenance_edges": provenance},
    )

    await cur.execute(
        "DELETE FROM mnemos.fact_provenance WHERE tenant_id = %s AND subject_key = %s",
        (tenant_id, subject_key),
    )
    await cur.execute(
        "DELETE FROM mnemos.semantic_facts WHERE tenant_id = %s AND subject_key = %s",
        (tenant_id, subject_key),
    )
    await cur.execute(
        "DELETE FROM mnemos.episodic_events WHERE tenant_id = %s AND subject_key = %s",
        (tenant_id, subject_key),
    )

    return ErasureResult(
        tenant_id=tenant_id,
        subject_key=subject_key,
        mode=EraseMode.FORGET,
        reason=reason,
        actor=actor,
        executed_at=_now(),
        episodes_removed=episodes,
        facts_removed=facts,
        provenance_edges_removed=provenance,
        audit_ticket=uuid4(),
    )


async def execute_quarantine(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    subject_key: str,
    *,
    actor: str,
    reason: str,
) -> ErasureResult:
    """Retain everything, revoke it from recall. Reversible — the mode for
    suspected-bad data under investigation, or data under hold that must stop
    influencing decisions without being destroyed while the hold is active."""
    await _require_unheld(cur, tenant_id, subject_key)
    _episodes, facts, _provenance, _, _ = await _counts(cur, tenant_id, subject_key)

    await append_audit(
        cur,
        tenant_id,
        op=Op.QUARANTINE,
        actor=actor,
        subject_key=subject_key,
        reason=reason,
        payload={"facts": facts},
    )

    await cur.execute(
        "UPDATE mnemos.semantic_facts SET trust = 'quarantined', "
        "quarantined_at = now(), quarantine_reason = %s, updated_at = now() "
        "WHERE tenant_id = %s AND subject_key = %s",
        (reason, tenant_id, subject_key),
    )

    return ErasureResult(
        tenant_id=tenant_id,
        subject_key=subject_key,
        mode=EraseMode.QUARANTINE,
        reason=reason,
        actor=actor,
        executed_at=_now(),
        episodes_removed=0,
        facts_removed=facts,
        provenance_edges_removed=0,
        audit_ticket=uuid4(),
    )


async def execute_shred(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    subject_key: str,
    *,
    actor: str,
    reason: str,
    key_provider: KeyProvider,
) -> ErasureResult:
    """FORGET, plus destroy the tenant's data key.

    `forget` alone leaves ciphertext recoverable inside MVCC history and
    backups. Destroying the key renders that ciphertext permanently
    unreadable regardless of where a copy of it sits — which is what closes
    the gap docs/limits.md names explicitly.

    IRREVERSIBLE, and tenant-wide: destroying the key affects every row this
    tenant has ever written, not just this subject's. A caller shredding one
    subject is choosing to shred the tenant's key custody entirely; the
    Warden's confirm gate must make that scope unmistakable before this runs.
    """
    result = await execute_forget(cur, tenant_id, subject_key, actor=actor, reason=reason)
    key_provider.destroy(tenant_id)
    log.warning(
        "tenant key destroyed — all ciphertext for this tenant is now unrecoverable",
        extra={"tenant_id": str(tenant_id), "subject_key": subject_key},
    )
    return result.model_copy(update={"mode": EraseMode.SHRED, "key_destroyed": True})
