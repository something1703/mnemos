"""Shared fixtures for Custodian tests — run against a live local cluster."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mnemos_engine.db import Database

LOCAL_DSN = "postgresql://root@localhost:26257/mnemos?sslmode=disable"


class ScriptedChat:
    """A `ChatClient` that returns pre-scripted JSON values in order, so a
    test controls exactly what "the model" says without a network call or a
    real API key. Raises `AssertionError` (loudly, in the test, not silently
    in production code) if a test scripts fewer responses than the code
    under test actually asks for."""

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


class StubFactWriter:
    """A `FactWriter` that records what it was asked to remember, instead of
    making a real write-scoped MCP call — `tests/custodian/test_sweep.py`'s
    unit-level tests use this; the real `McpFactWriter` path is covered
    separately against a fake in-process API server."""

    def __init__(self) -> None:
        self.remembered: list[dict[str, Any]] = []

    async def remember_ops_finding(
        self, tenant_id: uuid.UUID, *, subject_key: str, content: str, session_id: uuid.UUID
    ) -> uuid.UUID:
        event_id = uuid.uuid4()
        self.remembered.append(
            {
                "tenant_id": tenant_id,
                "subject_key": subject_key,
                "content": content,
                "session_id": session_id,
                "event_id": event_id,
            }
        )
        return event_id


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    database = Database(LOCAL_DSN, min_size=1, max_size=8)
    try:
        await database.open()
    except Exception as exc:
        pytest.skip(f"local CockroachDB unavailable ({exc}). Run: make db-local")
    yield database
    await database.close()


@pytest.fixture
async def tenant(db: Database) -> uuid.UUID:
    tenant_id = uuid.uuid4()

    async def create(cur):
        await cur.execute(
            "INSERT INTO mnemos.tenants (tenant_id, slug, display_name) VALUES (%s, %s, %s)",
            (tenant_id, f"custodian-{tenant_id.hex[:8]}", "Custodian test"),
        )

    await db.transaction(None, create, label="create_tenant")
    return tenant_id
