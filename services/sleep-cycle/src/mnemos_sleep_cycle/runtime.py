"""Process-wide runtime: one connection pool, one embedder, one chat client.

`envelope=Envelope(LocalKeyWrapper())` matches `services/api/src/mnemos_api/
runtime.py` exactly and on purpose — this service decrypts episodes the API
wrote and writes facts the API must later decrypt for `recall`. A different
wrapper configuration between the two services would not fail loudly; it would
fail as every fact this service writes being silently undecryptable, which is
indistinguishable from data corruption until someone notices `recall` never
returns anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from mnemos_engine.crypto import Envelope, LocalKeyWrapper
from mnemos_engine.db import Database
from mnemos_engine.embeddings import Embedder, OpenAIEmbedder
from mnemos_engine.llm import ChatClient, OpenAIChatClient

from .config import Settings, get_settings

log = logging.getLogger("mnemos.sleep_cycle.runtime")


@dataclass
class Runtime:
    settings: Settings
    db: Database
    embedder: Embedder
    chat: ChatClient
    envelope: Envelope

    async def close(self) -> None:
        await self.db.close()


async def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or get_settings()

    db = Database(settings.database_url, min_size=1, max_size=4)
    await db.open()

    if not settings.database_url_is_pipeline_role:
        log.warning(
            "running against the admin database role — see MNEMOS_DB_URL_PIPELINE "
            "in .env.example. Fine for local development, wrong for a deployment."
        )

    embedder = OpenAIEmbedder(
        api_key=settings.openai_api_key,
        model=settings.embed_model,
        dimensions=settings.embed_dimensions,
    )
    chat = OpenAIChatClient(api_key=settings.openai_api_key, model=settings.distill_model)

    return Runtime(
        settings=settings,
        db=db,
        embedder=embedder,
        chat=chat,
        envelope=Envelope(LocalKeyWrapper()),
    )
