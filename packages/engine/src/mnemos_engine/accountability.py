"""Pillar II — actions, and the depositions that explain them.

`record_action` binds a decision to the recalls that caused it. `explain` walks
that binding backwards into a verifiable document: action ← recalls ← facts as
they stood at recall time ← provenance ← source episodes, with the audit row for
every step and the checkpoint that covers them.

The load-bearing detail is *as they stood at recall time*. A deposition that
reported today's values would answer the wrong question — "what does the system
believe now" instead of "what was the agent told when it decided". The recall
log stores the score components and trust state at the moment of the recall
precisely so this distinction survives.

Source episode **content is never included**, only its hash. A deposition may
legitimately cross a jurisdiction that the underlying record may not (invariant
4), so it carries proof-of-derivation rather than the derived-from bytes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from .ledger import append_audit
from .models import Op

log = logging.getLogger("mnemos.accountability")


class Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DepositionEpisode(Base):
    event_id: UUID
    subject_key: str
    event_type: str
    source_trust: str
    home_region: str
    occurred_at: datetime
    content_hash: str
    """Hex digest of the plaintext. Proves which bytes a fact derives from
    without moving them across a border."""
    still_exists: bool
    """False when the episode has since been erased. The deposition survives the
    erasure of what it describes — that is the point of hashing rather than
    quoting."""


class DepositionFact(Base):
    fact_id: UUID
    subject_key: str
    fact_kind: str
    home_region: str

    trust_at_recall: str
    similarity_at_recall: float | None
    strength_at_recall: float | None
    confidence_at_recall: float | None
    score_at_recall: float | None

    trust_now: str | None
    """None when the fact has since been deleted."""
    changed_since: bool
    revoked_since: bool
    superseded_since: bool

    text_hash: str | None
    provenance: list[DepositionEpisode] = Field(default_factory=list)


class DepositionAudit(Base):
    shard_id: int
    seq: int
    op: str
    actor: str
    committed_at: datetime
    entry_hash: str


class Deposition(Base):
    """The document you hand a regulator.

    Self-contained and hash-verifiable: every claim carries the digest that backs
    it, and `docs/ledger.md` tells a stranger how to recompute them.
    """

    tenant_id: UUID
    action_id: UUID
    action_type: str
    description: str
    agent_id: UUID | None
    session_id: UUID | None
    subject_key: str | None
    declared_at: datetime

    contaminated: bool
    contaminated_at: datetime | None
    contamination_note: str | None

    facts: list[DepositionFact] = Field(default_factory=list)
    audit_trail: list[DepositionAudit] = Field(default_factory=list)

    checkpoint_seq: int | None = None
    merkle_root: str | None = None
    anchor_uri: str | None = None
    """Where the covering Merkle root is anchored outside the database. Absent
    means the proof rests entirely on data an administrator could rewrite —
    which the console must show rather than hide."""

    def summary(self) -> str:
        lines = [
            f"Action {self.action_id} — {self.action_type}",
            f"  {self.description}",
            f"  declared {self.declared_at.isoformat()}",
            f"  influenced by {len(self.facts)} fact(s)",
        ]
        if self.contaminated:
            lines.append(f"  CONTAMINATED: {self.contamination_note}")
        for fact in self.facts:
            flags = []
            if fact.revoked_since:
                flags.append("revoked since")
            if fact.superseded_since:
                flags.append("superseded since")
            elif fact.changed_since:
                flags.append("changed since")
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            lines.append(
                f"    - {fact.subject_key} ({fact.fact_kind}) "
                f"trust={fact.trust_at_recall} score={fact.score_at_recall:.4f}{suffix}"
                if fact.score_at_recall is not None
                else f"    - {fact.subject_key} ({fact.fact_kind})"
            )
        if self.anchor_uri:
            lines.append(f"  anchored at {self.anchor_uri}")
        else:
            lines.append("  NOT YET ANCHORED — proof rests on database state alone")
        return "\n".join(lines)


async def record_action(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    action_type: str,
    description: str,
    recall_ids: list[UUID],
    actor: str,
    agent_id: UUID | None = None,
    session_id: UUID | None = None,
    subject_key: str | None = None,
) -> UUID:
    """Declare that an agent did something, and why.

    `recall_ids` come straight from a `RecallResult`. An action with no recalls
    is permitted — not every decision is memory-driven — but an action that
    *was* memory-driven and does not say so is invisible to `revoke_source`,
    and will not be flagged when its evidence is later withdrawn.
    """
    action_id = uuid4()

    await append_audit(
        cur,
        tenant_id,
        op=Op.RECORD_ACTION,
        actor=actor,
        subject_key=subject_key,
        payload={
            "action_id": str(action_id),
            "action_type": action_type,
            "recall_ids": [str(r) for r in recall_ids],
        },
    )

    await cur.execute(
        """
        INSERT INTO mnemos.action_log
            (tenant_id, action_id, agent_id, session_id, subject_key, action_type, description)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (tenant_id, action_id, agent_id, session_id, subject_key, action_type, description),
    )

    for recall_id in recall_ids:
        await cur.execute(
            "INSERT INTO mnemos.action_recalls (tenant_id, action_id, recall_id) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (tenant_id, action_id, recall_id),
        )

    return action_id


async def explain(cur: psycopg.AsyncCursor, tenant_id: UUID, action_id: UUID) -> Deposition | None:
    """Build the verifiable causal chain behind one action."""
    await cur.execute(
        "SELECT action_type, description, agent_id, session_id, subject_key, declared_at, "
        "       contaminated_at, contaminated_by "
        "FROM mnemos.action_log WHERE tenant_id = %s AND action_id = %s",
        (tenant_id, action_id),
    )
    action = await cur.fetchone()
    if action is None:
        return None

    (
        action_type,
        description,
        agent_id,
        session_id,
        subject_key,
        declared_at,
        contaminated_at,
        contaminated_by,
    ) = action

    await cur.execute(
        """
        SELECT r.fact_id, r.trust_at, r.similarity, r.strength_at, r.confidence_at, r.score
        FROM mnemos.action_recalls ar
        JOIN mnemos.recall_log r
          ON r.tenant_id = ar.tenant_id AND r.recall_id = ar.recall_id
        WHERE ar.tenant_id = %s AND ar.action_id = %s
        ORDER BY r.score DESC NULLS LAST
        """,
        (tenant_id, action_id),
    )
    recalled = await cur.fetchall()

    facts: list[DepositionFact] = []
    for fact_id, trust_at, similarity, strength_at, confidence_at, score in recalled:
        facts.append(
            await _build_fact(
                cur,
                tenant_id,
                fact_id,
                trust_at=str(trust_at) if trust_at else "unknown",
                similarity=_maybe_float(similarity),
                strength=_maybe_float(strength_at),
                confidence=_maybe_float(confidence_at),
                score=_maybe_float(score),
            )
        )

    audit_trail = await _audit_trail(cur, tenant_id, subject_key, declared_at)
    checkpoint_seq, merkle_root, anchor_uri = await _covering_checkpoint(
        cur, tenant_id, declared_at
    )

    note: str | None = None
    if contaminated_at is not None:
        note = (
            "This decision was influenced by memory that has since been revoked. "
            f"Revocation {contaminated_by} withdrew the evidence it rested on."
        )

    return Deposition(
        tenant_id=tenant_id,
        action_id=action_id,
        action_type=action_type,
        description=description,
        agent_id=agent_id,
        session_id=session_id,
        subject_key=subject_key,
        declared_at=declared_at,
        contaminated=contaminated_at is not None,
        contaminated_at=contaminated_at,
        contamination_note=note,
        facts=facts,
        audit_trail=audit_trail,
        checkpoint_seq=checkpoint_seq,
        merkle_root=merkle_root,
        anchor_uri=anchor_uri,
    )


async def _build_fact(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    fact_id: UUID,
    *,
    trust_at: str,
    similarity: float | None,
    strength: float | None,
    confidence: float | None,
    score: float | None,
) -> DepositionFact:
    await cur.execute(
        "SELECT subject_key, fact_kind, home_region, trust, text_hash, "
        "       revoked_at, superseded_by, strength "
        "FROM mnemos.semantic_facts WHERE tenant_id = %s AND fact_id = %s",
        (tenant_id, fact_id),
    )
    row = await cur.fetchone()

    if row is None:
        # The fact was erased after it influenced the action. The deposition
        # must still describe it — an erasure that also destroys the record of
        # what it affected would defeat the point of keeping one.
        return DepositionFact(
            fact_id=fact_id,
            subject_key="(erased)",
            fact_kind="(erased)",
            home_region="(erased)",
            trust_at_recall=trust_at,
            similarity_at_recall=similarity,
            strength_at_recall=strength,
            confidence_at_recall=confidence,
            score_at_recall=score,
            trust_now=None,
            changed_since=True,
            revoked_since=False,
            superseded_since=False,
            text_hash=None,
        )

    subject_key, fact_kind, home_region, trust_now, text_hash, revoked_at, superseded_by, _ = row

    await cur.execute(
        """
        SELECT e.event_id, e.subject_key, e.event_type, e.source_trust, e.home_region,
               e.occurred_at, e.content_hash
        FROM mnemos.fact_provenance p
        JOIN mnemos.episodic_events e
          ON e.tenant_id = p.tenant_id AND e.event_id = p.event_id
             AND e.subject_key = p.subject_key
        WHERE p.tenant_id = %s AND p.fact_id = %s
        """,
        (tenant_id, fact_id),
    )
    episodes = [
        DepositionEpisode(
            event_id=r[0],
            subject_key=r[1],
            event_type=r[2],
            source_trust=str(r[3]),
            home_region=r[4],
            occurred_at=r[5],
            content_hash=bytes(r[6]).hex(),
            still_exists=True,
        )
        for r in await cur.fetchall()
    ]

    return DepositionFact(
        fact_id=fact_id,
        subject_key=subject_key,
        fact_kind=fact_kind,
        home_region=home_region,
        trust_at_recall=trust_at,
        similarity_at_recall=similarity,
        strength_at_recall=strength,
        confidence_at_recall=confidence,
        score_at_recall=score,
        trust_now=str(trust_now),
        changed_since=str(trust_now) != trust_at,
        revoked_since=revoked_at is not None,
        superseded_since=superseded_by is not None,
        text_hash=bytes(text_hash).hex() if text_hash else None,
        provenance=episodes,
    )


async def _audit_trail(
    cur: psycopg.AsyncCursor, tenant_id: UUID, subject_key: str | None, until: datetime
) -> list[DepositionAudit]:
    if subject_key is None:
        return []
    await cur.execute(
        "SELECT shard_id, seq, op, actor, committed_at, entry_hash FROM mnemos.audit_chain "
        "WHERE tenant_id = %s AND subject_key = %s AND committed_at <= %s "
        "ORDER BY shard_id, seq",
        (tenant_id, subject_key, until),
    )
    return [
        DepositionAudit(
            shard_id=int(r[0]),
            seq=int(r[1]),
            op=str(r[2]),
            actor=str(r[3]),
            committed_at=r[4],
            entry_hash=bytes(r[5]).hex(),
        )
        for r in await cur.fetchall()
    ]


async def _covering_checkpoint(
    cur: psycopg.AsyncCursor, tenant_id: UUID, at: datetime
) -> tuple[int | None, str | None, str | None]:
    """The first checkpoint committed at or after the action.

    Deliberately the *covering* one rather than the latest: a root taken before
    the action proves nothing about it, and the latest root proves rather more
    than the action alone.
    """
    await cur.execute(
        "SELECT checkpoint_seq, merkle_root, anchor_uri FROM mnemos.chain_checkpoints "
        "WHERE tenant_id = %s AND covers_through >= %s ORDER BY checkpoint_seq LIMIT 1",
        (tenant_id, at),
    )
    row = await cur.fetchone()
    if row is None:
        return None, None, None
    return int(row[0]), bytes(row[1]).hex(), row[2]


def _maybe_float(value: Any) -> float | None:
    return float(value) if value is not None else None


async def build_verifiable_export(
    cur: psycopg.AsyncCursor, tenant_id: UUID, action_id: UUID
) -> dict[str, Any] | None:
    """Everything `explain()` returns, plus the raw chain data a caller needs
    to recompute the hashes themselves — the payload behind the self-verifying
    HTML deposition export (PHASE_06 6.7).

    `Deposition.audit_trail` (from `explain()`) is filtered to this subject's
    own entries — right for *display*, wrong for *verification*: proving
    `prev_hash` linkage needs the shard's complete, unfiltered sequence,
    because seq numbers interleave across every subject that happens to hash
    to the same shard. So this fetches, for every shard `audit_trail`
    touches, the full ordered chain from seq 1 — exactly what
    `mnemos_engine.ledger.verify_chain` reads server-side — plus the covering
    checkpoint's complete `shard_heads`, so a caller can redo both halves of
    verification (`docs/ledger.md` §5.1 and §5.2) with zero trust in this
    process's own arithmetic.

    Returns `None` under the same condition `explain()` does: no such action.
    """
    deposition = await explain(cur, tenant_id, action_id)
    if deposition is None:
        return None

    shard_ids = sorted({a.shard_id for a in deposition.audit_trail})
    chain_entries: dict[str, list[dict[str, Any]]] = {}
    for shard_id in shard_ids:
        await cur.execute(
            "SELECT seq, payload, payload_hash, prev_hash, entry_hash "
            "FROM mnemos.audit_chain WHERE tenant_id = %s AND shard_id = %s ORDER BY seq",
            (tenant_id, shard_id),
        )
        chain_entries[str(shard_id)] = [
            {
                "seq": int(seq),
                "payload": payload,
                "payload_hash": bytes(payload_hash).hex(),
                "prev_hash": bytes(prev_hash).hex(),
                "entry_hash": bytes(entry_hash).hex(),
            }
            for seq, payload, payload_hash, prev_hash, entry_hash in await cur.fetchall()
        ]

    checkpoint: dict[str, Any] | None = None
    if deposition.checkpoint_seq is not None:
        await cur.execute(
            "SELECT merkle_root, shard_heads, entry_count, anchor_uri, anchored_at "
            "FROM mnemos.chain_checkpoints WHERE tenant_id = %s AND checkpoint_seq = %s",
            (tenant_id, deposition.checkpoint_seq),
        )
        row = await cur.fetchone()
        if row is not None:
            merkle_root_hex, shard_heads, entry_count, anchor_uri, anchored_at = row
            checkpoint = {
                "checkpoint_seq": deposition.checkpoint_seq,
                "merkle_root": bytes(merkle_root_hex).hex(),
                "shard_heads": dict(shard_heads),
                "entry_count": int(entry_count),
                "anchor_uri": anchor_uri,
                "anchored_at": anchored_at.isoformat() if anchored_at else None,
            }

    return {
        "deposition": deposition,
        "chain_entries": chain_entries,
        "checkpoint": checkpoint,
    }
