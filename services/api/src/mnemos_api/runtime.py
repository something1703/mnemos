"""Process-wide runtime: connection pools, engine, Warden, embedder.

The important structure here is that there are **two** database pools, not
one:

  * `db` connects as a role with no DELETE grant anywhere (`mnemos_api`,
    migration 011). Every ordinary memory operation runs through it, so a bug
    in tool dispatch cannot destroy data — the database would refuse.
  * `warden_db` is the privileged connection, touched only after an admin
    scope check has already passed.

If they are the same DSN, privilege separation is not active. The service
still starts, because refusing to run would make local development miserable,
but it logs a warning at startup and reports `privilege_separation: false` in
`memory_stats`. The posture is visible rather than assumed — in deployment
(Phase 04.4) the Warden becomes a separate Lambda behind an IAM boundary,
which is the stronger form of the same idea.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from mnemos_engine.crypto import Envelope, LocalKeyWrapper
from mnemos_engine.db import Database
from mnemos_engine.embeddings import Embedder, FakeEmbedder
from mnemos_engine.engine import MnemosEngine
from mnemos_warden.keys import KeyProvider, LocalKeyProvider
from mnemos_warden.warden import Warden

from .config import Settings, get_settings
from .embedding import OpenAIEmbedder

log = logging.getLogger("mnemos.api.runtime")


@dataclass
class Runtime:
    settings: Settings
    db: Database
    warden_db: Database
    engine: MnemosEngine
    warden: Warden
    embedder: Embedder
    key_provider: KeyProvider

    async def close(self) -> None:
        await self.db.close()
        if self.warden_db is not self.db:
            await self.warden_db.close()


def _build_embedder(settings: Settings) -> Embedder:
    """Real embeddings when a key is present, deterministic fakes otherwise.

    Falling back to FakeEmbedder rather than failing lets the whole test suite
    and the local demos run with no API key and no network — but it is a
    genuinely different system, so it is logged rather than silent.
    """
    if settings.model_provider == "openai" and settings.openai_api_key:
        log.info(
            "embeddings: OpenAI %s @ %d dimensions",
            settings.embed_model,
            settings.embed_dimensions,
        )
        return OpenAIEmbedder(
            api_key=settings.openai_api_key,
            model=settings.embed_model,
            dimensions=settings.embed_dimensions,
        )

    log.warning(
        "embeddings: FakeEmbedder (deterministic, NOT semantic) — no %s key configured. "
        "Retrieval quality is meaningless in this mode; plumbing still works.",
        settings.model_provider,
    )
    return FakeEmbedder(dimension=settings.embed_dimensions)


async def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or get_settings()

    db = Database(settings.database_url, min_size=1, max_size=8)
    await db.open()

    if settings.privilege_separation_active:
        assert settings.warden_database_url is not None
        warden_db = Database(settings.warden_database_url, min_size=1, max_size=4)
        await warden_db.open()
        log.info("privilege separation ACTIVE: Warden uses a separate database role")
    else:
        warden_db = db
        log.warning(
            "privilege separation INACTIVE: the Warden shares the API's database "
            "connection. Set MNEMOS_DB_URL_WARDEN to a role that holds DELETE while "
            "MNEMOS_DB_URL points at one that does not. Reported as "
            "privilege_separation=false in memory_stats."
        )

    embedder = _build_embedder(settings)
    key_provider = LocalKeyProvider()

    engine = MnemosEngine(
        db,
        embedder=embedder,
        envelope=Envelope(LocalKeyWrapper()),
        actor="api",
        region=settings.region,
    )
    warden = Warden(warden_db, key_provider=key_provider)

    return Runtime(
        settings=settings,
        db=db,
        warden_db=warden_db,
        engine=engine,
        warden=warden,
        embedder=embedder,
        key_provider=key_provider,
    )
