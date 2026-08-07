"""Procedural memory — learned playbooks.

The most dangerous tier in any agentic memory system, and the reason it is the
most locked down here. A fact is a belief; a skill is an *instruction the agent
will execute*. An agent that can write its own procedure and then run it has no
meaningful security boundary — the loop from "read attacker-controlled text" to
"perform attacker-chosen action" closes with nothing in between.

So agent- and external-authored skill versions land `quarantined` and
`find_skill` will not return them. Promotion requires a human or an independent
corroborating outcome. This is the single strictest gate in the system, and it
is strict on purpose: the cost of a false negative is a playbook that takes an
extra day to become usable, and the cost of a false positive is an agent
executing an attacker's instructions.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from uuid import UUID, uuid4

import psycopg
from pydantic import BaseModel, ConfigDict

from .crypto import DecryptionFailed, Envelope, row_aad
from .embeddings import Embedder, to_pgvector
from .ledger import append_audit
from .models import Op, SourceTrust, Trust

log = logging.getLogger("mnemos.procedural")

DEMOTION_FAILURE_THRESHOLD = 3
"""Consecutive failures before a trusted skill is automatically demoted.

A playbook that keeps not working should stop being offered without anyone
having to notice. Three rather than one, because a single failure is as likely
to mean an unusual incident as a bad runbook."""


class Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SkillVersion(Base):
    tenant_id: UUID
    skill_id: UUID
    name: str
    version: int
    trust: Trust
    source_trust: SourceTrust
    created_at: datetime
    playbook: str | None = None
    playbook_hash: bytes | None = None
    successes: int = 0
    failures: int = 0

    @property
    def is_executable(self) -> bool:
        """Whether an agent may act on this.

        Deliberately narrower than "recallable": a contested *fact* is useful
        information, a contested *procedure* is a hazard.
        """
        return self.trust is Trust.TRUSTED

    @property
    def fitness(self) -> float:
        total = self.successes + self.failures
        return self.successes / total if total else 0.0


async def learn_skill(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    name: str,
    playbook: str,
    task_description: str,
    source_trust: SourceTrust,
    embedder: Embedder,
    envelope: Envelope,
    actor: str,
    fact_ids: list[UUID] | None = None,
) -> SkillVersion:
    """Store a new version of a playbook.

    Versioned, never overwritten: a superseded runbook is exactly what a
    deposition needs when explaining why an agent did something last month.

    `fact_ids` cite the facts this playbook rests on, so `blast_radius` can
    reach the procedural tier. A skill with no citations is invisible to
    revocation — it will keep being offered after the evidence behind it is
    withdrawn.
    """
    subject_key = f"skill:{name}"

    await cur.execute(
        "SELECT skill_id FROM mnemos.skills WHERE tenant_id = %s AND name = %s",
        (tenant_id, name),
    )
    row = await cur.fetchone()
    skill_id = row[0] if row else uuid4()

    await cur.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM mnemos.skill_versions "
        "WHERE tenant_id = %s AND skill_id = %s",
        (tenant_id, skill_id),
    )
    version_row = await cur.fetchone()
    version = int(version_row[0]) if version_row else 1

    # The gate. Anything an LLM or a third party wrote is quarantined on arrival.
    trust = Trust.TRUSTED if source_trust.is_trusted_on_arrival else Trust.QUARANTINED

    await append_audit(
        cur,
        tenant_id,
        op=Op.LEARN_SKILL,
        actor=actor,
        subject_key=subject_key,
        payload={
            "skill_id": str(skill_id),
            "version": version,
            "source_trust": str(source_trust),
            "trust": str(trust),
            "cites": [str(f) for f in (fact_ids or [])],
        },
    )

    if row is None:
        await cur.execute(
            "INSERT INTO mnemos.skills (tenant_id, skill_id, name, description) "
            "VALUES (%s, %s, %s, %s)",
            (tenant_id, skill_id, name, task_description[:500]),
        )

    ciphertext, wrapped = envelope.encrypt(playbook, aad=row_aad(tenant_id, subject_key))
    playbook_hash = hashlib.sha256(playbook.encode("utf-8")).digest()
    task_vector = (await embedder.embed([task_description]))[0]

    await cur.execute(
        """
        INSERT INTO mnemos.skill_versions
            (tenant_id, skill_id, version, playbook_ciphertext, playbook_dek_wrapped,
             playbook_hash, task_embedding, source_trust, trust, quarantined_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING created_at
        """,
        (
            tenant_id,
            skill_id,
            version,
            ciphertext,
            wrapped,
            playbook_hash,
            to_pgvector(task_vector),
            str(source_trust),
            str(trust),
            None if trust is Trust.TRUSTED else "now()",
        ),
    )
    created = await cur.fetchone()

    for fact_id in fact_ids or []:
        await cur.execute(
            "INSERT INTO mnemos.skill_provenance (tenant_id, skill_id, version, fact_id) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (tenant_id, skill_id, version, fact_id),
        )

    if trust is Trust.QUARANTINED:
        log.info(
            "skill quarantined on arrival",
            extra={"skill": name, "version": version, "source_trust": str(source_trust)},
        )

    return SkillVersion(
        tenant_id=tenant_id,
        skill_id=skill_id,
        name=name,
        version=version,
        trust=trust,
        source_trust=source_trust,
        created_at=created[0] if created else datetime.now(),  # noqa: DTZ005
        playbook=playbook,
        playbook_hash=playbook_hash,
    )


async def find_skill(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    task: str,
    *,
    embedder: Embedder,
    envelope: Envelope,
    include_quarantined: bool = False,
    k: int = 3,
) -> list[SkillVersion]:
    """Find playbooks matching a task, by task-embedding similarity.

    Only the latest version of each skill is considered, and only executable
    ones unless explicitly asked otherwise. `include_quarantined=True` exists
    for the console — so a human can review what the agent is being denied —
    and must never be wired to an agent-facing tool.
    """
    task_vector = (await embedder.embed([task]))[0]
    literal = to_pgvector(task_vector)
    allowed = (
        [str(t) for t in Trust]
        if include_quarantined
        else [str(Trust.TRUSTED), str(Trust.CORROBORATED)]
    )

    await cur.execute(
        """
        WITH latest AS (
            SELECT skill_id, MAX(version) AS version
            FROM mnemos.skill_versions
            WHERE tenant_id = %s AND revoked_at IS NULL
            GROUP BY skill_id
        )
        SELECT v.skill_id, s.name, v.version, v.trust, v.source_trust, v.created_at,
               v.playbook_ciphertext, v.playbook_dek_wrapped, v.playbook_hash
        FROM mnemos.skill_versions v
        JOIN latest l ON l.skill_id = v.skill_id AND l.version = v.version
        JOIN mnemos.skills s ON s.tenant_id = v.tenant_id AND s.skill_id = v.skill_id
        WHERE v.tenant_id = %s AND v.trust = ANY(%s) AND v.task_embedding IS NOT NULL
        ORDER BY v.task_embedding <-> %s::VECTOR
        LIMIT %s
        """,
        (tenant_id, tenant_id, allowed, literal, k),
    )

    found: list[SkillVersion] = []
    for r in await cur.fetchall():
        name = r[1]
        playbook: str | None
        try:
            playbook = envelope.decrypt(
                bytes(r[6]), bytes(r[7]), aad=row_aad(tenant_id, f"skill:{name}")
            )
        except DecryptionFailed:
            playbook = None

        successes, failures = await _fitness(cur, tenant_id, r[0], int(r[2]))
        found.append(
            SkillVersion(
                tenant_id=tenant_id,
                skill_id=r[0],
                name=name,
                version=int(r[2]),
                trust=Trust(r[3]),
                source_trust=SourceTrust(r[4]),
                created_at=r[5],
                playbook=playbook,
                playbook_hash=bytes(r[8]),
                successes=successes,
                failures=failures,
            )
        )
    return found


async def record_outcome(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    skill_id: UUID,
    version: int,
    success: bool,
    actor: str,
    latency_ms: int | None = None,
    notes: str | None = None,
) -> Trust:
    """Record how a playbook performed, and demote it if it keeps failing.

    Returns the skill's trust state after the update. Demotion is automatic and
    audited; promotion is not — earning trust back requires a human or an
    independent corroboration, because a skill that failed three times and then
    succeeded once has not proven anything.
    """
    await cur.execute(
        "SELECT name FROM mnemos.skills WHERE tenant_id = %s AND skill_id = %s",
        (tenant_id, skill_id),
    )
    name_row = await cur.fetchone()
    subject_key = f"skill:{name_row[0]}" if name_row else f"skill:{skill_id}"

    await append_audit(
        cur,
        tenant_id,
        op=Op.RECORD_ACTION,
        actor=actor,
        subject_key=subject_key,
        payload={"skill_id": str(skill_id), "version": version, "success": success},
    )

    await cur.execute(
        "INSERT INTO mnemos.skill_outcomes "
        "(tenant_id, skill_id, version, success, latency_ms, notes) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (tenant_id, skill_id, version, success, latency_ms, notes),
    )

    await cur.execute(
        "SELECT trust FROM mnemos.skill_versions "
        "WHERE tenant_id = %s AND skill_id = %s AND version = %s",
        (tenant_id, skill_id, version),
    )
    trust_row = await cur.fetchone()
    current = Trust(trust_row[0]) if trust_row else Trust.QUARANTINED

    if success or current is Trust.QUARANTINED:
        return current

    recent = await _recent_failures(cur, tenant_id, skill_id, version)
    if recent < DEMOTION_FAILURE_THRESHOLD:
        return current

    await append_audit(
        cur,
        tenant_id,
        op=Op.DEMOTE,
        actor="system:fitness",
        subject_key=subject_key,
        reason=f"{recent} consecutive failures",
        payload={"skill_id": str(skill_id), "version": version, "from": str(current)},
    )
    await cur.execute(
        "UPDATE mnemos.skill_versions SET trust = 'quarantined', quarantined_at = now() "
        "WHERE tenant_id = %s AND skill_id = %s AND version = %s",
        (tenant_id, skill_id, version),
    )
    log.warning(
        "skill demoted after repeated failure",
        extra={"skill_id": str(skill_id), "version": version, "failures": recent},
    )
    return Trust.QUARANTINED


async def _fitness(
    cur: psycopg.AsyncCursor, tenant_id: UUID, skill_id: UUID, version: int
) -> tuple[int, int]:
    await cur.execute(
        "SELECT count(*) FILTER (WHERE success), count(*) FILTER (WHERE NOT success) "
        "FROM mnemos.skill_outcomes "
        "WHERE tenant_id = %s AND skill_id = %s AND version = %s",
        (tenant_id, skill_id, version),
    )
    row = await cur.fetchone()
    return (int(row[0]), int(row[1])) if row else (0, 0)


async def _recent_failures(
    cur: psycopg.AsyncCursor, tenant_id: UUID, skill_id: UUID, version: int
) -> int:
    """Count consecutive failures at the tail.

    Consecutive, not total: a playbook with fifty successes and three scattered
    failures is fine, while one that has failed the last three times is not,
    even if its lifetime ratio looks healthy.
    """
    await cur.execute(
        "SELECT success FROM mnemos.skill_outcomes "
        "WHERE tenant_id = %s AND skill_id = %s AND version = %s "
        "ORDER BY recorded_at DESC LIMIT %s",
        (tenant_id, skill_id, version, DEMOTION_FAILURE_THRESHOLD),
    )
    streak = 0
    for (success,) in await cur.fetchall():
        if success:
            break
        streak += 1
    return streak
