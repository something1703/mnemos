"""Typed models for the governance plane."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EraseMode(StrEnum):
    """Erasure is a spectrum, not a boolean. See PHASE_06.4 and docs/limits.md."""

    REDACT = "redact"
    """Content ciphertext tombstoned; row, provenance, and audit history
    retained. Use when the record that something existed must survive but its
    content must not."""

    FORGET = "forget"
    """Episodes, derived facts, vector index entries, and provenance edges
    deleted atomically. Audit row retained. The GDPR Art. 17 mode."""

    QUARANTINE = "quarantine"
    """Rows retained, revoked from all recall, marked contested/quarantined.
    Reversible — for suspected-bad data under investigation."""

    SHRED = "shred"
    """FORGET plus destruction of the tenant's data key. Closes the backup and
    MVCC-history gap that FORGET alone cannot reach. Irreversible."""


class LegalHold(Base):
    tenant_id: UUID
    hold_id: UUID
    subject_key: str
    matter_reference: str
    placed_by: str
    placed_at: datetime
    expires_at: datetime | None
    released_by: str | None = None
    released_at: datetime | None = None
    release_reason: str | None = None

    @property
    def is_active(self) -> bool:
        return self.released_at is None


class ErasurePreview(Base):
    """What a mode would destroy, computed before anything is touched.

    The console shows this before the confirm button, and the SAME query runs
    again inside the destructive transaction — there is no gap between what
    was previewed and what is destroyed, because both come from one code path.
    """

    tenant_id: UUID
    subject_key: str
    mode: EraseMode

    episode_count: int
    fact_count: int
    provenance_edge_count: int
    recall_log_count: int
    skill_citation_count: int

    blocked_by_hold: LegalHold | None = None
    """Present means this preview cannot be executed. The Warden must refuse,
    not warn."""

    @property
    def is_blocked(self) -> bool:
        return self.blocked_by_hold is not None

    @property
    def total_rows(self) -> int:
        return self.episode_count + self.fact_count + self.provenance_edge_count


class ErasureResult(Base):
    tenant_id: UUID
    subject_key: str
    mode: EraseMode
    reason: str
    actor: str
    executed_at: datetime

    episodes_removed: int
    facts_removed: int
    provenance_edges_removed: int

    audit_ticket: UUID
    key_destroyed: bool = False


class RevocationRecord(Base):
    tenant_id: UUID
    revocation_id: UUID
    source_event_ids: list[UUID]
    reason: str
    actor: str
    created_at: datetime
    radius_manifest: dict[str, object] = Field(default_factory=dict)
