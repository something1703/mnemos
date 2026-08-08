"""Fixtures for the API layer, against a live local cluster.

Deliberately builds a real Runtime (real pools, real engine, real Warden)
rather than mocking. The properties under test here — that a write key cannot
reach the Warden, that scope errors are legible — are properties of the wiring,
and a mocked wiring proves nothing about the real one.

Embeddings use FakeEmbedder: no API key needed, no network, no spend, and the
tests assert on authorisation and plumbing rather than retrieval quality.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from mnemos_api.config import Settings
from mnemos_api.context import clear_principal, set_principal
from mnemos_api.keys import Principal, Scope, mint_key
from mnemos_api.runtime import Runtime, build_runtime

LOCAL_DSN = "postgresql://root@localhost:26257/mnemos?sslmode=disable"


def _settings(**overrides: object) -> Settings:
    base = {
        "database_url": LOCAL_DSN,
        "warden_database_url": None,
        "region": "us-east-1",
        # No key => FakeEmbedder. Keeps the suite offline and free.
        "openai_api_key": None,
        "embed_model": "text-embedding-3-small",
        "embed_dimensions": 1024,
        "distill_model": None,
        "model_provider": "openai",
        "anchor_bucket": None,
        "chain_shards": 16,
        "host": "127.0.0.1",
        "port": 8000,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
async def runtime() -> AsyncIterator[Runtime]:
    try:
        rt = await build_runtime(_settings())
    except Exception as exc:
        pytest.skip(f"local CockroachDB unavailable ({exc}). Run: make db-local")
    yield rt
    await rt.close()


@pytest.fixture
async def tenant(runtime: Runtime) -> uuid.UUID:
    tenant_id = uuid.uuid4()

    async def create(cur):
        await cur.execute(
            "INSERT INTO mnemos.tenants (tenant_id, slug, display_name) VALUES (%s, %s, %s)",
            (tenant_id, f"api-{tenant_id.hex[:8]}", "API test"),
        )

    await runtime.db.transaction(None, create, label="create_tenant")
    return tenant_id


@pytest.fixture
def as_principal() -> Callable[..., object]:
    """Bind a caller for the duration of a test, and always unbind after.

    Yields a factory rather than a fixed principal so a single test can check
    the same call under two different scopes — which is exactly what the scope
    matrix needs.
    """
    tokens: list[object] = []

    def _bind(tenant_id: uuid.UUID, scope: Scope, label: str = "test") -> Principal:
        principal = Principal(tenant_id=tenant_id, key_id=uuid.uuid4(), scope=scope, label=label)
        tokens.append(set_principal(principal))
        return principal

    yield _bind  # type: ignore[misc]

    # Unbind rather than reset-by-token: an async test runs in a different
    # context than this sync fixture, so the tokens are not resettable here.
    # What matters is that no principal survives into the next test.
    clear_principal()


@pytest.fixture
async def minted_keys(runtime: Runtime, tenant: uuid.UUID) -> dict[str, str]:
    """One real, database-backed key per scope."""
    created: dict[str, str] = {}

    for scope in (Scope.READ, Scope.WRITE, Scope.ADMIN):

        async def run(cur, scope=scope):
            return await mint_key(cur, tenant, scope=scope, label=f"test-{scope.label}")

        plaintext, _ = await runtime.db.transaction(tenant, run, label="mint")
        created[scope.label] = plaintext

    return created
