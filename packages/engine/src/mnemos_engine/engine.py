"""The Fabric — remember, recall, recall_as_of.

Reads and writes memory. Contains no destructive operation: `make
no-delete-in-engine` fails the build if one appears here, because destruction is
the Warden's job alone (invariant 1).

Two design choices shape everything below.

**The write path does no AI work.** `remember` encrypts, resolves residency, and
inserts. No embedding call, no model call, nothing that can be throttled or
rate-limited by a third party. Memory intake therefore survives a total Bedrock
outage — proven in `demos/resilience.sh`, and the reason consolidation is a
separate asynchronous pipeline rather than something that happens on write.

**Nothing an LLM writes is recallable until something independent agrees.**
Facts enter at `unverified` and the trust gate excludes them by default.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Json

from . import residency
from .canonical import GENESIS_HASH
from .crypto import DecryptionFailed, Envelope, row_aad
from .db import Database
from .embeddings import Embedder, reciprocal_rank_fusion, to_pgvector
from .errors import OutsideTemporalWindow, ResidencyViolation
from .ledger import append_audit
from .models import (
    ContestedPair,
    Episode,
    Fact,
    Op,
    ProvenanceEdge,
    RecallResult,
    ScoreBreakdown,
    ScoredFact,
    SourceTrust,
    Trust,
)

log = logging.getLogger("mnemos.engine")

TRUST_WEIGHTS = {
    Trust.TRUSTED: 1.0,
    Trust.CORROBORATED: 0.85,
    # Only ever reachable with include_unverified=True. Weighted low enough that
    # an unverified fact never outranks a corroborated one on similarity alone.
    Trust.UNVERIFIED: 0.25,
    Trust.CONTESTED: 0.4,
    Trust.QUARANTINED: 0.0,
}

RECALLABLE = (Trust.TRUSTED, Trust.CORROBORATED)
CANDIDATE_MULTIPLIER = 4
"""Over-fetch from each retriever before fusing. RRF needs depth to work with:
a fact ranked 7th by vectors and 9th by text should beat one ranked 1st by
vectors and absent from text, and that comparison is impossible at k=5."""


class MnemosEngine:
    def __init__(
        self,
        db: Database,
        *,
        embedder: Embedder,
        envelope: Envelope,
        actor: str = "system",
        region: str = "us-east-1",
    ) -> None:
        self._db = db
        self._embedder = embedder
        self._envelope = envelope
        self._actor = actor
        self._region = region
        """Where this process is running. Compared against a subject's home
        region so a write can never silently land in the wrong jurisdiction."""

    @property
    def envelope(self) -> Envelope:
        """The encryption boundary this engine writes through.

        Exposed so a caller composing a fact write outside of `remember` (the
        sleep cycle in Phase 05; tests that seed facts directly today) uses
        the SAME key custody the engine does, rather than constructing a
        second envelope that happens to agree — which is exactly the
        distinction a `shred` test needs to be meaningful.
        """
        return self._envelope

    # ------------------------------------------------------------------ write

    async def remember(
        self,
        tenant_id: UUID,
        *,
        subject_key: str,
        session_id: UUID,
        event_type: str,
        content: str,
        source_trust: SourceTrust,
        agent_id: UUID | None = None,
        idempotency_key: str | None = None,
        expire_at: datetime | None = None,
        s3_artifact: str | None = None,
    ) -> Episode:
        """Record one experience.

        `source_trust` is required, never inferred. It is the field the whole
        poisoning defense rests on, and a default would mean the most dangerous
        input — unattributed third-party text — silently inherits the safest
        label.
        """
        content_hash = hashlib.sha256(content.encode("utf-8")).digest()

        async def run(cur: psycopg.AsyncCursor) -> Episode:
            if idempotency_key is not None:
                existing = await self._find_by_idempotency(cur, tenant_id, idempotency_key)
                if existing is not None:
                    # No new row, and deliberately no new audit entry: a repeated
                    # request did not change anything, and a chain that grows on
                    # retries would report activity that never happened.
                    return existing

            decision = await residency.resolve(cur, tenant_id, subject_key)

            if decision.home_region != self._region:
                # Logged OUTSIDE this transaction — see the handler below. Writing
                # the denial here would be rolled back by the very exception that
                # denies it, so every refused crossing would vanish.
                raise ResidencyViolation(subject_key, decision.home_region, self._region)

            ciphertext, wrapped = self._envelope.encrypt(
                content, aad=row_aad(tenant_id, subject_key)
            )
            event_id = uuid4()

            await append_audit(
                cur,
                tenant_id,
                op=Op.REMEMBER,
                actor=self._actor,
                subject_key=subject_key,
                payload={
                    "event_id": str(event_id),
                    "event_type": event_type,
                    "source_trust": str(source_trust),
                    "home_region": decision.home_region,
                    "content_hash": content_hash.hex(),
                },
            )

            await cur.execute(
                """
                INSERT INTO mnemos.episodic_events
                    (tenant_id, subject_key, event_id, home_region, session_id, agent_id,
                     event_type, content_ciphertext, content_dek_wrapped, content_hash,
                     source_trust, s3_artifact, idempotency_key, expire_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING occurred_at
                """,
                (
                    tenant_id,
                    subject_key,
                    event_id,
                    decision.home_region,
                    session_id,
                    agent_id,
                    event_type,
                    ciphertext,
                    wrapped,
                    content_hash,
                    str(source_trust),
                    s3_artifact,
                    idempotency_key,
                    expire_at,
                ),
            )
            row = await cur.fetchone()
            assert row is not None  # RETURNING on a successful INSERT always yields a row

            return Episode(
                tenant_id=tenant_id,
                event_id=event_id,
                subject_key=subject_key,
                home_region=decision.home_region,
                session_id=session_id,
                event_type=event_type,
                source_trust=source_trust,
                content_hash=content_hash,
                occurred_at=row[0],
                content=content,
            )

        try:
            return await self._db.transaction(tenant_id, run, label="remember")
        except ResidencyViolation as violation:
            # A refusal nobody can see is indistinguishable from a request nobody
            # made. The denial is committed in its own transaction so it survives
            # the rollback, then the error propagates unchanged.
            await self._log_denied_crossing(tenant_id, subject_key, violation)
            raise

    async def _log_denied_crossing(
        self, tenant_id: UUID, subject_key: str, violation: ResidencyViolation
    ) -> None:
        async def run(cur: psycopg.AsyncCursor) -> None:
            decision = await residency.resolve(cur, tenant_id, subject_key)
            await residency.log_crossing(
                cur,
                tenant_id,
                subject_key=subject_key,
                from_region=violation.attempted_region,
                to_region=violation.home_region,
                decision=decision,
                allowed=False,
                requested_by=self._actor,
                denied_reason="writes must originate in the subject's home region",
            )

        await self._db.transaction(tenant_id, run, label="log_denied_crossing")

    async def _find_by_idempotency(
        self, cur: psycopg.AsyncCursor, tenant_id: UUID, key: str
    ) -> Episode | None:
        await cur.execute(
            "SELECT event_id, subject_key, home_region, session_id, event_type, "
            "       source_trust, content_hash, occurred_at, consolidated_at "
            "FROM mnemos.episodic_events WHERE tenant_id = %s AND idempotency_key = %s",
            (tenant_id, key),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return Episode(
            tenant_id=tenant_id,
            event_id=row[0],
            subject_key=row[1],
            home_region=row[2],
            session_id=row[3],
            event_type=row[4],
            source_trust=SourceTrust(row[5]),
            content_hash=bytes(row[6]),
            occurred_at=row[7],
            consolidated_at=row[8],
        )

    # ------------------------------------------------------------------- read

    async def recall(
        self,
        tenant_id: UUID,
        query: str,
        *,
        subject_key: str | None = None,
        k: int = 8,
        include_unverified: bool = False,
        session_id: UUID | None = None,
        agent_id: UUID | None = None,
        reinforce: bool = True,
    ) -> RecallResult:
        """Hybrid retrieval with a trust gate, reinforcement, and a recall log.

        Facts at `unverified` or `quarantined` are excluded unless explicitly
        requested, and even then they are returned tagged so a caller knows it is
        drinking unfiltered water.
        """
        query_vector = (await self._embedder.embed([query]))[0]
        query_hash = hashlib.sha256(query.encode("utf-8")).digest()

        async def run(cur: psycopg.AsyncCursor) -> RecallResult:
            scored, withheld = await self._hybrid_search(
                cur, tenant_id, query, query_vector, subject_key, k, include_unverified
            )
            contested = await self._contested_pairs(cur, tenant_id, scored)
            episodes = await self._session_tail(cur, tenant_id, session_id, subject_key)

            recall_ids: list[UUID] = []
            if scored:
                recall_ids = await self._log_recall(
                    cur, tenant_id, scored, query_hash, session_id, agent_id
                )
                if reinforce:
                    await self._reinforce(cur, tenant_id, scored)

            return RecallResult(
                facts=scored,
                episodes=episodes,
                contested=contested,
                unverified_withheld=withheld,
                recall_ids=recall_ids,
            )

        return await self._db.transaction(tenant_id, run, label="recall")

    async def recall_as_of(
        self,
        tenant_id: UUID,
        query: str,
        as_of: datetime,
        *,
        subject_key: str | None = None,
        k: int = 8,
        include_unverified: bool = False,
    ) -> RecallResult:
        """What the agent would have recalled at `as_of`.

        Reconstructs the facts as they stood: their text, their strengths, their
        trust states, before any later supersession or revocation. This is the
        Accountability pillar, and it exists because CockroachDB keeps MVCC
        history — no other memory layer can offer it without building a
        versioning system by hand.

        Two deliberate differences from `recall`:

        * **Read-only.** `AS OF SYSTEM TIME` transactions cannot write, so there
          is no reinforcement and no recall_log entry. Asking what the agent knew
          must not change what it knows.
        * **Fails loudly past the GC window.** Silently answering from `now()`
          would produce a deposition that looks right and is wrong, which is the
          worst failure this API could have.
        """
        gc_ttl = await self._db.gc_ttl_seconds()
        earliest = datetime.now(UTC) - timedelta(seconds=gc_ttl)
        if as_of < earliest:
            raise OutsideTemporalWindow(as_of, earliest, gc_ttl)

        query_vector = (await self._embedder.embed([query]))[0]
        timestamp = as_of.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")

        async def run(cur: psycopg.AsyncCursor) -> RecallResult:
            scored, withheld = await self._hybrid_search(
                cur, tenant_id, query, query_vector, subject_key, k, include_unverified
            )
            return RecallResult(
                facts=scored,
                unverified_withheld=withheld,
                as_of=as_of,
            )

        return await self._db.transaction(
            tenant_id,
            run,
            label="recall_as_of",
            read_only=True,
            as_of=f"'{timestamp}'",
        )

    # --------------------------------------------------------------- internals

    async def _hybrid_search(
        self,
        cur: psycopg.AsyncCursor,
        tenant_id: UUID,
        query: str,
        query_vector: list[float],
        subject_key: str | None,
        k: int,
        include_unverified: bool,
    ) -> tuple[list[ScoredFact], int]:
        allowed = list(Trust) if include_unverified else list(RECALLABLE)
        allowed_values = [str(t) for t in allowed if t != Trust.QUARANTINED]
        depth = k * CANDIDATE_MULTIPLIER
        literal = to_pgvector(query_vector)

        subject_filter = "AND subject_key = %s" if subject_key else ""
        vector_params: list[object] = [tenant_id, allowed_values]
        if subject_key:
            vector_params.append(subject_key)
        vector_params.extend([literal, depth])

        await cur.execute(
            f"""
            SELECT fact_id, embedding <-> %s::VECTOR AS distance
            FROM mnemos.semantic_facts
            WHERE tenant_id = %s AND trust = ANY(%s) AND revoked_at IS NULL
                  AND superseded_by IS NULL AND embedding IS NOT NULL {subject_filter}
            ORDER BY embedding <-> %s::VECTOR
            LIMIT %s
            """,  # noqa: S608 - subject_filter is a fixed string, not user input
            (
                literal,
                tenant_id,
                allowed_values,
                *([subject_key] if subject_key else []),
                literal,
                depth,
            ),
        )
        vector_rows = await cur.fetchall()
        distances = {str(row[0]): float(row[1]) for row in vector_rows}
        vector_ranking = [str(row[0]) for row in vector_rows]

        await cur.execute(
            f"""
            SELECT fact_id
            FROM mnemos.semantic_facts
            WHERE tenant_id = %s AND trust = ANY(%s) AND revoked_at IS NULL
                  AND superseded_by IS NULL AND tsv @@ plainto_tsquery(%s) {subject_filter}
            ORDER BY ts_rank(tsv, plainto_tsquery(%s)) DESC
            LIMIT %s
            """,  # noqa: S608
            (
                tenant_id,
                allowed_values,
                query,
                *([subject_key] if subject_key else []),
                query,
                depth,
            ),
        )
        text_ranking = [str(row[0]) for row in await cur.fetchall()]

        fused = reciprocal_rank_fusion([vector_ranking, text_ranking])
        top_ids = sorted(fused, key=lambda i: fused[i], reverse=True)[:k]
        if not top_ids:
            return [], await self._count_withheld(cur, tenant_id, subject_key)

        await cur.execute(
            """
            SELECT fact_id, subject_key, home_region, fact_kind, trust, strength,
                   confidence, corroboration_count, recall_count, created_at,
                   text_ciphertext, text_dek_wrapped, text_hash, superseded_by,
                   contested_with, quarantined_at, revoked_at, last_recalled_at
            FROM mnemos.semantic_facts
            WHERE tenant_id = %s AND fact_id = ANY(%s)
            """,
            (tenant_id, [UUID(i) for i in top_ids]),
        )

        scored: list[ScoredFact] = []
        for row in await cur.fetchall():
            fact_id = str(row[0])
            trust = Trust(row[4])
            distance = distances.get(fact_id)
            # L2 distance on unit vectors maps monotonically to similarity;
            # 1/(1+d) keeps it in (0,1] without pretending to be cosine.
            similarity = 1.0 / (1.0 + distance) if distance is not None else 0.5

            text: str | None = None
            try:
                text = self._envelope.decrypt(
                    bytes(row[10]), bytes(row[11]), aad=row_aad(tenant_id, row[1])
                )
            except DecryptionFailed:
                # Expected after a shred: the row survives, its content does not.
                text = None

            scored.append(
                ScoredFact(
                    fact=Fact(
                        tenant_id=tenant_id,
                        fact_id=row[0],
                        subject_key=row[1],
                        home_region=row[2],
                        fact_kind=row[3],
                        trust=trust,
                        strength=float(row[5]),
                        confidence=float(row[6]),
                        corroboration_count=int(row[7]),
                        recall_count=int(row[8]),
                        created_at=row[9],
                        text=text,
                        text_hash=bytes(row[12]),
                        superseded_by=row[13],
                        contested_with=row[14],
                        quarantined_at=row[15],
                        revoked_at=row[16],
                        last_recalled_at=row[17],
                    ),
                    breakdown=ScoreBreakdown(
                        similarity=similarity,
                        strength=float(row[5]),
                        confidence=float(row[6]),
                        trust_weight=TRUST_WEIGHTS[trust],
                    ),
                    provenance=await self._provenance(cur, tenant_id, row[0]),
                )
            )

        scored.sort(key=lambda s: s.score, reverse=True)
        return scored, await self._count_withheld(cur, tenant_id, subject_key)

    async def _count_withheld(
        self, cur: psycopg.AsyncCursor, tenant_id: UUID, subject_key: str | None
    ) -> int:
        """How much memory exists but has not earned recall.

        Surfaced so a caller seeing thin results learns the difference between
        "nothing is known" and "nothing is trusted yet" — which is also the
        leading indicator of a poisoning attempt.
        """
        if subject_key:
            await cur.execute(
                "SELECT count(*) FROM mnemos.semantic_facts WHERE tenant_id = %s "
                "AND subject_key = %s AND trust IN ('unverified', 'quarantined')",
                (tenant_id, subject_key),
            )
        else:
            await cur.execute(
                "SELECT count(*) FROM mnemos.semantic_facts WHERE tenant_id = %s "
                "AND trust IN ('unverified', 'quarantined')",
                (tenant_id,),
            )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def _provenance(
        self, cur: psycopg.AsyncCursor, tenant_id: UUID, fact_id: UUID
    ) -> list[ProvenanceEdge]:
        await cur.execute(
            "SELECT fact_id, event_id, subject_key, weight FROM mnemos.fact_provenance "
            "WHERE tenant_id = %s AND fact_id = %s",
            (tenant_id, fact_id),
        )
        return [
            ProvenanceEdge(fact_id=r[0], event_id=r[1], subject_key=r[2], weight=float(r[3]))
            for r in await cur.fetchall()
        ]

    async def _contested_pairs(
        self, cur: psycopg.AsyncCursor, tenant_id: UUID, scored: list[ScoredFact]
    ) -> list[ContestedPair]:
        """Surface disagreement instead of resolving it silently.

        A memory layer that picks a winner behind the caller's back has made a
        judgement it cannot be held to. Returning both sides with their evidence
        lets the agent — or the human reading the console — decide.
        """
        pairs: list[ContestedPair] = []
        for item in scored:
            counterpart_id = item.fact.contested_with
            if counterpart_id is None:
                continue
            await cur.execute(
                "SELECT fact_id, subject_key, home_region, fact_kind, trust, strength, "
                "       confidence, corroboration_count, recall_count, created_at "
                "FROM mnemos.semantic_facts WHERE tenant_id = %s AND fact_id = %s",
                (tenant_id, counterpart_id),
            )
            row = await cur.fetchone()
            if row is None:
                continue
            pairs.append(
                ContestedPair(
                    left=item.fact,
                    right=Fact(
                        tenant_id=tenant_id,
                        fact_id=row[0],
                        subject_key=row[1],
                        home_region=row[2],
                        fact_kind=row[3],
                        trust=Trust(row[4]),
                        strength=float(row[5]),
                        confidence=float(row[6]),
                        corroboration_count=int(row[7]),
                        recall_count=int(row[8]),
                        created_at=row[9],
                    ),
                    left_evidence=item.provenance,
                    right_evidence=await self._provenance(cur, tenant_id, row[0]),
                )
            )
        return pairs

    async def _session_tail(
        self,
        cur: psycopg.AsyncCursor,
        tenant_id: UUID,
        session_id: UUID | None,
        subject_key: str | None,
        limit: int = 5,
    ) -> list[Episode]:
        if session_id is None:
            return []
        await cur.execute(
            "SELECT event_id, subject_key, home_region, session_id, event_type, "
            "       source_trust, content_hash, occurred_at, consolidated_at "
            "FROM mnemos.episodic_events WHERE tenant_id = %s AND session_id = %s "
            "ORDER BY occurred_at DESC LIMIT %s",
            (tenant_id, session_id, limit),
        )
        return [
            Episode(
                tenant_id=tenant_id,
                event_id=r[0],
                subject_key=r[1],
                home_region=r[2],
                session_id=r[3],
                event_type=r[4],
                source_trust=SourceTrust(r[5]),
                content_hash=bytes(r[6]),
                occurred_at=r[7],
                consolidated_at=r[8],
            )
            for r in await cur.fetchall()
        ]

    async def _log_recall(
        self,
        cur: psycopg.AsyncCursor,
        tenant_id: UUID,
        scored: list[ScoredFact],
        query_hash: bytes,
        session_id: UUID | None,
        agent_id: UUID | None,
    ) -> list[UUID]:
        """Record what was returned, to whom, at what score.

        Append-only, and the reason `explain()` can work at all. Note that the
        score components are stored as they were *at this moment* — a deposition
        must report what the agent was told, not what the fact says today.
        """
        recall_id = uuid4()
        for item in scored:
            await cur.execute(
                """
                INSERT INTO mnemos.recall_log
                    (tenant_id, recall_id, fact_id, agent_id, session_id, query_hash,
                     similarity, strength_at, confidence_at, trust_at, score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    recall_id,
                    item.fact.fact_id,
                    agent_id,
                    session_id,
                    query_hash,
                    item.breakdown.similarity,
                    item.breakdown.strength,
                    item.breakdown.confidence,
                    str(item.fact.trust),
                    item.score,
                ),
            )
        return [recall_id]

    async def _reinforce(
        self, cur: psycopg.AsyncCursor, tenant_id: UUID, scored: list[ScoredFact]
    ) -> None:
        """Recalling a fact strengthens it — memory that gets used decays slower.

        Audited like every other mutation, and the reason the audit row comes
        first: without it the trigger rejects the UPDATE.
        """
        fact_ids = [s.fact.fact_id for s in scored]
        await append_audit(
            cur,
            tenant_id,
            op=Op.REINFORCE,
            actor=self._actor,
            subject_key=scored[0].fact.subject_key,
            payload={"fact_ids": [str(f) for f in fact_ids], "count": len(fact_ids)},
        )
        await cur.execute(
            "UPDATE mnemos.semantic_facts "
            "SET recall_count = recall_count + 1, last_recalled_at = now(), "
            "    strength = strength + 0.1, updated_at = now() "
            "WHERE tenant_id = %s AND fact_id = ANY(%s)",
            (tenant_id, fact_ids),
        )


__all__ = ["GENESIS_HASH", "Json", "MnemosEngine"]
