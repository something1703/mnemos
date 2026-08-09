"""Shared fixtures for the sleep-cycle test suite.

These run against the local Docker cluster, same as tests/engine and
tests/warden — an invariant proven against a fake database proves nothing.
Nothing here calls a real model: `ScriptedChat` stands in for `ChatClient`
wherever a test needs to control exactly what "the model" says.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from mnemos_engine.crypto import Envelope, LocalKeyWrapper, row_aad
from mnemos_engine.db import Database
from mnemos_engine.embeddings import Embedder, FakeEmbedder, to_pgvector
from mnemos_engine.engine import MnemosEngine
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Episode, Op, SourceTrust, Trust

LOCAL_DSN = "postgresql://root@localhost:26257/mnemos?sslmode=disable"


@pytest.fixture
async def db() -> Database:
    database = Database(LOCAL_DSN, min_size=1, max_size=8)
    try:
        await database.open()
    except Exception as exc:
        pytest.skip(f"local CockroachDB unavailable ({exc}). Run: make db-local")
    yield database
    await database.close()


@pytest.fixture
def envelope() -> Envelope:
    return Envelope(LocalKeyWrapper())


@pytest.fixture
def embedder() -> Embedder:
    return FakeEmbedder()


@pytest.fixture
def engine(db: Database, embedder: Embedder, envelope: Envelope) -> MnemosEngine:
    return MnemosEngine(db, embedder=embedder, envelope=envelope, actor="test", region="us-east-1")


@pytest.fixture
async def tenant(db: Database) -> UUID:
    tenant_id = uuid4()

    async def create(cur: psycopg.AsyncCursor) -> None:
        await cur.execute(
            "INSERT INTO mnemos.tenants (tenant_id, slug, display_name, default_region) "
            "VALUES (%s, %s, %s, 'us-east-1')",
            (tenant_id, f"sc-{tenant_id.hex[:8]}", "Sleep cycle test"),
        )

    await db.transaction(None, create, label="create_tenant")
    return tenant_id


class ScriptedChat:
    """A `ChatClient` that returns pre-scripted JSON values in order, so a
    test controls exactly what "the model" says without a network call or a
    real API key. Raises `AssertionError` (loudly, in the test, not silently
    in production code) if a test scripts fewer responses than the code under
    test actually asks for.
    """

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, str]] = []

    async def complete_json(
        self, *, system: str, user: str, temperature: float = 0.0, max_output_tokens: int = 2048
    ) -> Any:
        self.calls.append({"system": system, "user": user})
        if not self._responses:
            raise AssertionError(
                f"ScriptedChat ran out of responses after {len(self.calls)} call(s); "
                "the code under test asked for more than the test scripted"
            )
        return self._responses.pop(0)


async def remember_episode(
    engine: MnemosEngine,
    tenant_id: UUID,
    *,
    subject_key: str,
    session_id: UUID,
    content: str,
    source_trust: SourceTrust,
    event_type: str = "note",
) -> Episode:
    """A real, encrypted episode via the real write path — not a hand-rolled
    INSERT with placeholder bytes. `consolidate.py` decrypts what it reads, so
    a test episode that cannot be decrypted proves nothing about it."""
    return await engine.remember(
        tenant_id,
        subject_key=subject_key,
        session_id=session_id,
        event_type=event_type,
        content=content,
        source_trust=source_trust,
    )


async def seed_fact(
    db: Database,
    tenant_id: UUID,
    *,
    subject_key: str,
    text: str,
    embedder: Embedder,
    envelope: Envelope,
    trust: Trust = Trust.UNVERIFIED,
    confidence: float = 0.8,
    strength: float = 1.0,
    corroboration_count: int = 0,
    source_event_id: UUID | None = None,
    fact_kind: str = "note",
) -> UUID:
    """Write a fact directly — bypassing `consolidate.py` entirely — for tests
    that exercise `revise.py`/`corroboration.py` in isolation. Provenance is
    written before the fact row, exactly like `consolidate._insert_fact`, so a
    fact seeded here at `trust='trusted'` still satisfies migration 010's
    `require_provenance` trigger.
    """
    fact_id = uuid4()
    vector = (await embedder.embed([text]))[0]
    ciphertext, wrapped = envelope.encrypt(text, aad=row_aad(tenant_id, subject_key))
    text_hash = hashlib.sha256(text.encode("utf-8")).digest()

    async def run(cur: psycopg.AsyncCursor) -> None:
        await append_audit(
            cur,
            tenant_id,
            op=Op.CONSOLIDATE,
            actor="test",
            subject_key=subject_key,
            payload={"fact_id": str(fact_id), "seeded": True},
        )
        if source_event_id is not None:
            await cur.execute(
                "INSERT INTO mnemos.fact_provenance (tenant_id, fact_id, event_id, subject_key) "
                "VALUES (%s, %s, %s, %s)",
                (tenant_id, fact_id, source_event_id, subject_key),
            )
        await cur.execute(
            """
            INSERT INTO mnemos.semantic_facts
                (tenant_id, fact_id, home_region, subject_key, fact_kind, text_ciphertext,
                 text_dek_wrapped, text_hash, embedding, tsv, trust, confidence, strength,
                 corroboration_count)
            VALUES (%s, %s, 'us-east-1', %s, %s, %s, %s, %s, %s::VECTOR,
                    to_tsvector('english', %s), %s, %s, %s, %s)
            """,
            (
                tenant_id,
                fact_id,
                subject_key,
                fact_kind,
                ciphertext,
                wrapped,
                text_hash,
                to_pgvector(vector),
                text,
                str(trust),
                confidence,
                strength,
                corroboration_count,
            ),
        )

    await db.transaction(tenant_id, run, label="seed_fact")
    return fact_id


def controlled_similarity_vector(
    base: list[float], other: list[float], cosine: float
) -> list[float]:
    """A unit vector at exactly `cosine` similarity to `base`, built by
    Gram-Schmidt against `other` and blending.

    `FakeEmbedder` is deliberately non-semantic (see its docstring) — two
    different strings land at essentially uncorrelated vectors, which is fine
    for testing NOVEL (similarity near zero) and REINFORCE (identical text,
    similarity exactly 1.0), but gives no way to engineer the specific
    similarities the SUPERSEDE/CONTEST boundary in `revise.py` needs to be
    tested at all. This constructs one directly instead.
    """

    def norm(v: list[float]) -> list[float]:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    b = norm(base)
    o = norm(other)
    dot = sum(x * y for x, y in zip(b, o, strict=True))
    orthogonal = norm([oy - dot * by for by, oy in zip(b, o, strict=True)])
    sin = math.sqrt(max(0.0, 1.0 - cosine * cosine))
    return [cosine * bx + sin * ox for bx, ox in zip(b, orthogonal, strict=True)]


def utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "LOCAL_DSN",
    "ScriptedChat",
    "controlled_similarity_vector",
    "remember_episode",
    "seed_fact",
    "utc_now",
]
