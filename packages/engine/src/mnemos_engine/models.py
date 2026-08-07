"""Typed models for everything that crosses a boundary.

Scores are returned decomposed rather than as a single number. A ranking an
operator cannot inspect is a ranking nobody can debug, and "why did the agent
recall that?" is a question this system exists to answer.
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SourceTrust(StrEnum):
    """Where a memory came from. Required at write time, never inferred."""

    SYSTEM = "system"
    """Deterministic internal process. Trusted on arrival."""

    OPERATOR = "operator"
    """An authenticated human. Trusted on arrival."""

    AGENT = "agent"
    """LLM-generated. Untrusted: must be corroborated before it is recallable."""

    EXTERNAL = "external"
    """Third-party, user-supplied, or tool output. Untrusted, and the most
    likely carrier of a poisoning attempt."""

    @property
    def is_trusted_on_arrival(self) -> bool:
        return self in (SourceTrust.SYSTEM, SourceTrust.OPERATOR)


class Trust(StrEnum):
    """The trust lattice. Only CORROBORATED and TRUSTED reach recall."""

    UNVERIFIED = "unverified"
    CORROBORATED = "corroborated"
    TRUSTED = "trusted"
    CONTESTED = "contested"
    QUARANTINED = "quarantined"

    @property
    def is_recallable(self) -> bool:
        return self in (Trust.CORROBORATED, Trust.TRUSTED)


class Op(StrEnum):
    """Audit operations. Mirrors the CHECK constraint in migration 007."""

    REMEMBER = "remember"
    CONSOLIDATE = "consolidate"
    REINFORCE = "reinforce"
    PROMOTE = "promote"
    DEMOTE = "demote"
    RECALL = "recall"
    DECAY = "decay"
    SUPERSEDE = "supersede"
    CONTEST = "contest"
    QUARANTINE = "quarantine"
    REVOKE = "revoke"
    REDACT = "redact"
    FORGET = "forget"
    SHRED = "shred"
    HOLD = "hold"
    RELEASE_HOLD = "release_hold"
    POLICY = "policy"
    CHECKPOINT = "checkpoint"
    LEARN_SKILL = "learn_skill"
    RECORD_ACTION = "record_action"
    PROPOSAL = "proposal"


class Projection(StrEnum):
    """What may cross a jurisdictional boundary for a subject class."""

    NONE = "none"
    DERIVED = "derived"
    AGGREGATE = "aggregate"


class Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Episode(Base):
    tenant_id: UUID
    event_id: UUID
    subject_key: str
    home_region: str
    session_id: UUID
    event_type: str
    source_trust: SourceTrust
    content_hash: bytes
    occurred_at: datetime
    consolidated_at: datetime | None = None
    content: str | None = None
    """Decrypted only when the caller's residency policy permits it. None means
    the row exists but this session may not read its raw content."""


class ProvenanceEdge(Base):
    fact_id: UUID
    event_id: UUID
    subject_key: str
    weight: float = 1.0


class ScoreBreakdown(Base):
    """Every component of a recall score, so ranking is inspectable.

    The console renders this on hover; a deposition records the values that held
    at the moment of the recall, not the values that hold now.
    """

    similarity: float
    strength: float
    confidence: float
    trust_weight: float

    @property
    def score(self) -> float:
        # ln(1+strength) so reinforcement has diminishing returns: a fact
        # recalled a thousand times should not bury one recalled ten times.
        return self.similarity * math.log1p(self.strength) * self.confidence * self.trust_weight


class Fact(Base):
    tenant_id: UUID
    fact_id: UUID
    subject_key: str
    home_region: str
    fact_kind: str
    trust: Trust
    strength: float
    confidence: float
    corroboration_count: int
    recall_count: int
    created_at: datetime
    text: str | None = None
    text_hash: bytes | None = None
    superseded_by: UUID | None = None
    contested_with: UUID | None = None
    quarantined_at: datetime | None = None
    revoked_at: datetime | None = None
    last_recalled_at: datetime | None = None


class ScoredFact(Base):
    fact: Fact
    breakdown: ScoreBreakdown
    provenance: list[ProvenanceEdge] = Field(default_factory=list)

    @property
    def score(self) -> float:
        return self.breakdown.score


class ContestedPair(Base):
    """Two facts that disagree, surfaced rather than silently resolved.

    An agent that knows it is unsure is more useful than one that guesses, and a
    memory layer that picks a winner behind the caller's back has made a
    judgement it cannot be held to.
    """

    left: Fact
    right: Fact
    left_evidence: list[ProvenanceEdge] = Field(default_factory=list)
    right_evidence: list[ProvenanceEdge] = Field(default_factory=list)


class RecallResult(Base):
    facts: list[ScoredFact] = Field(default_factory=list)
    episodes: list[Episode] = Field(default_factory=list)
    contested: list[ContestedPair] = Field(default_factory=list)
    unverified_withheld: int = 0
    """How many matching facts were excluded for being unverified or
    quarantined. Surfaced so a caller seeing thin results learns that memory
    exists but has not earned recall, rather than concluding nothing is known."""
    as_of: datetime | None = None
    recall_ids: list[UUID] = Field(default_factory=list)
    """Written to recall_log. record_action() cites these to bind a decision to
    the memory that caused it."""


class AuditRow(Base):
    tenant_id: UUID
    shard_id: int
    seq: int
    ticket: UUID
    op: Op
    actor: str
    subject_key: str | None
    reason: str | None
    payload: dict[str, object]
    payload_hash: bytes
    prev_hash: bytes
    entry_hash: bytes
    committed_at: datetime


class Checkpoint(Base):
    tenant_id: UUID
    checkpoint_seq: int
    merkle_root: bytes
    shard_heads: dict[str, dict[str, object]]
    entry_count: int
    covers_through: datetime
    anchor_uri: str | None = None
    anchored_at: datetime | None = None

    @property
    def is_anchored(self) -> bool:
        """An unanchored checkpoint is not yet evidence of anything.

        The console must not present one as proof, and mnemos-attest treats it
        as absent.
        """
        return self.anchored_at is not None


class VerificationResult(Base):
    tenant_id: UUID
    valid: bool
    entries_checked: int
    shards_checked: int
    checkpoints_checked: int
    broken_at: tuple[int, int] | None = None
    """(shard_id, seq) of the first link that failed to recompute."""
    detail: str | None = None
