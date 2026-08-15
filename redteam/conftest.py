"""Fixtures for the red-team suite.

Deliberately separate from `tests/`: these run against dedicated `redteam-*`
tenants so a failed attack cannot leave debris in a demo tenant, and so the
suite can be pointed at the deployed cluster later without touching anything
a judge will look at.

Same local Docker cluster as the rest of the suite (`make db-local &&
make db-migrate`). An attack proven against a fake database proves nothing,
for exactly the reason `tests/conftest.py` gives about invariants.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from mnemos_engine.crypto import Envelope, LocalKeyWrapper
from mnemos_engine.db import Database
from mnemos_engine.embeddings import Embedder, FakeEmbedder
from mnemos_engine.engine import MnemosEngine

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
    return MnemosEngine(
        db, embedder=embedder, envelope=envelope, actor="redteam", region="us-east-1"
    )


@pytest.fixture
async def redteam_tenant(db: Database) -> UUID:
    """A throwaway tenant per test, named so debris is identifiable."""
    tenant_id = uuid4()

    async def create(cur: psycopg.AsyncCursor) -> None:
        await cur.execute(
            "INSERT INTO mnemos.tenants (tenant_id, slug, display_name, default_region) "
            "VALUES (%s, %s, %s, %s)",
            (tenant_id, f"redteam-{tenant_id.hex[:8]}", "Red team", "us-east-1"),
        )

    await db.transaction(None, create, label="create_redteam_tenant")
    return tenant_id


class CompromisedDistiller:
    """A distiller that has already lost the prompt-injection fight.

    It does not read the payload and decide — it simply emits whatever the
    attacker asked for, at maximum confidence, every time. That is the point:
    the suite is not measuring whether a model resists (it will not, forever),
    it is measuring whether the architecture still refuses to *trust* the
    result. Every `unverified` verdict this suite records was earned against
    a model that was fully on the attacker's side.

    Also answers the contradiction judge, always with "not contradictory", so
    the attacker gets the most favourable possible revision outcome too.
    """

    def __init__(self, claim: str, *, fact_kind: str = "allergy") -> None:
        self.claim = claim
        self.fact_kind = fact_kind
        self.calls = 0

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
    ) -> Any:
        self.calls += 1
        # Matched on the judge prompt's opening sentence, not on the word
        # "contradict" — distill.md mentions contradictions too (it has a
        # `contradicts_hint` field), so the loose match sent the distiller the
        # judge's reply and every attack silently produced zero facts. A
        # red-team suite that fails to even land its payload proves nothing,
        # so this discriminator is deliberately tied to text only one of the
        # two prompts contains.
        if "you judge whether two short claims" in system.lower():
            return {"contradictory": False, "reason": "attacker says these agree"}
        return [
            {
                "fact_text": self.claim,
                "fact_kind": self.fact_kind,
                "confidence": 1.0,
                "source_indices": [1],
            }
        ]
