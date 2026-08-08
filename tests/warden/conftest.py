"""Shared fixtures for Warden tests — run against a live local cluster."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from mnemos_engine.crypto import Envelope
from mnemos_engine.db import Database
from mnemos_engine.embeddings import FakeEmbedder
from mnemos_engine.engine import MnemosEngine
from mnemos_warden.keys import LocalKeyProvider
from mnemos_warden.warden import Warden

LOCAL_DSN = "postgresql://root@localhost:26257/mnemos?sslmode=disable"


class _LiveKeyWrapper:
    """Resolves the tenant's current wrapper on every call rather than
    capturing one at construction time.

    Without this, an engine built before a `shred` would keep holding a
    reference to the (now-destroyed) real key object in memory and could
    still decrypt with it — which would test nothing, since Python garbage
    collection is not what makes shred work; `key_provider.destroy()` swapping
    the registered wrapper is. This mirrors how a real service resolves a
    tenant's key fresh per request rather than caching a key object across
    calls.
    """

    def __init__(self, provider: LocalKeyProvider, tenant_id: uuid.UUID) -> None:
        self._provider = provider
        self._tenant_id = tenant_id

    def wrap(self, dek: bytes) -> bytes:
        return self._provider.get_wrapper(self._tenant_id).wrap(dek)

    def unwrap(self, wrapped: bytes) -> bytes:
        return self._provider.get_wrapper(self._tenant_id).unwrap(wrapped)


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
def key_provider() -> LocalKeyProvider:
    return LocalKeyProvider()


@pytest.fixture
def warden(db: Database, key_provider: LocalKeyProvider) -> Warden:
    return Warden(db, key_provider=key_provider)


@pytest.fixture
def engine_for(db: Database, key_provider: LocalKeyProvider) -> Callable[..., MnemosEngine]:
    """A factory rather than a single instance, because each test needs its
    own tenant. Sharing `key_provider` with the `warden` fixture is the
    point: a `shred` test proves the SAME key custody the Warden destroyed is
    what the engine can no longer read through — not two independent mocks
    that happen to agree.

    Wrapped in `_LiveKeyWrapper` so the engine resolves the tenant's current
    key on every call rather than capturing one at construction time; see
    that class for why the distinction matters for a shred test specifically.

    Accepts `region=` because residency's write-side check (invariant 4,
    tested at the engine level) refuses a write whose engine region doesn't
    match the subject's home region. Residency tests need to WRITE into a
    subject's real home region and then simulate a READ from a DIFFERENT
    requester region — two independent axes, not one.
    """

    def make(tenant_id: uuid.UUID, *, region: str = "us-east-1") -> MnemosEngine:
        return MnemosEngine(
            db,
            embedder=FakeEmbedder(),
            envelope=Envelope(_LiveKeyWrapper(key_provider, tenant_id)),
            actor="test",
            region=region,
        )

    return make


@pytest.fixture
async def tenant(db: Database) -> uuid.UUID:
    tenant_id = uuid.uuid4()

    async def create(cur):
        await cur.execute(
            "INSERT INTO mnemos.tenants (tenant_id, slug, display_name) VALUES (%s, %s, %s)",
            (tenant_id, f"wd-{tenant_id.hex[:8]}", "Warden test"),
        )

    await db.transaction(None, create, label="create_tenant")
    return tenant_id
