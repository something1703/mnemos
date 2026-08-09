"""Wires together everything one real sweep needs — the Custodian's own DB
connection, the Cloud MCP client, the Cloud REST API client, the
interpretation model, and the write-scoped path back into memory — from
environment variables. `cli.py` is the only caller; this module holds no
`main()` of its own so the wiring is testable independent of argument
parsing and process exit codes.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

import httpx2
import psycopg
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mnemos_engine.db import Database
from mnemos_engine.llm import ChatClient, OpenAIChatClient

from .cloud_api import CloudApiClient
from .mcp_client import DEFAULT_ENDPOINT as CLOUD_MCP_DEFAULT_ENDPOINT
from .mcp_client import CustodianMcpClient
from .skills import Skill, load_all
from .sweep import FactWriter, McpFactWriter

log = logging.getLogger("mnemos.custodian.runtime")


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is not set. Copy .env.example to .env and fill it in, then:\n"
            "  set -a; source .env; set +a"
        )
    return value


@dataclass(frozen=True)
class CustodianSettings:
    db_url: str
    cockroach_mcp_url: str
    cockroach_service_account_key: str
    cockroach_cluster_id: str
    mnemos_api_url: str
    mnemos_api_key: str
    tenant_slug: str
    openai_api_key: str
    openai_model: str


def load_settings() -> CustodianSettings:
    return CustodianSettings(
        db_url=_require("MNEMOS_DB_URL_CUSTODIAN"),
        cockroach_mcp_url=os.environ.get("COCKROACH_MCP_URL") or CLOUD_MCP_DEFAULT_ENDPOINT,
        cockroach_service_account_key=_require("COCKROACH_SERVICE_ACCOUNT_KEY_CUSTODIAN"),
        cockroach_cluster_id=_require("COCKROACH_MCP_CLUSTER_ID"),
        mnemos_api_url=_require("MNEMOS_API_URL").rstrip("/"),
        mnemos_api_key=_require("MNEMOS_API_KEY_CUSTODIAN"),
        tenant_slug=_require("MNEMOS_CUSTODIAN_TENANT_SLUG"),
        openai_api_key=_require("OPENAI_API_KEY"),
        openai_model=os.environ.get("OPENAI_CUSTODIAN_MODEL") or _require("OPENAI_DISTILL_MODEL"),
    )


@dataclass
class CustodianRuntime:
    db: Database
    mcp: CustodianMcpClient
    cloud_api: CloudApiClient
    chat: ChatClient
    fact_writer: FactWriter
    tenant_id: UUID
    skills: dict[str, Skill]
    database: str = "mnemos"


async def _resolve_tenant(db: Database, slug: str) -> UUID:
    async def run(cur: psycopg.AsyncCursor) -> tuple[UUID] | None:
        await cur.execute("SELECT tenant_id FROM mnemos.tenants WHERE slug = %s", (slug,))
        return await cur.fetchone()

    row = await db.transaction(None, run, label="resolve_tenant", read_only=True)
    if row is None:
        raise RuntimeError(f"no tenant with slug {slug!r} — check MNEMOS_CUSTODIAN_TENANT_SLUG")
    return UUID(str(row[0]))


@asynccontextmanager
async def build_runtime(
    settings: CustodianSettings | None = None,
) -> AsyncIterator[CustodianRuntime]:
    """Opens every connection a sweep needs and closes all of them on exit,
    in the order that makes sense for a short-lived ECS task: DB first
    (needed to resolve the tenant), then the two Cloud connections and the
    API's own MCP session, torn down together via one `AsyncExitStack`.
    """
    settings = settings or load_settings()

    db = Database(settings.db_url, min_size=1, max_size=2)
    await db.open()
    try:
        tenant_id = await _resolve_tenant(db, settings.tenant_slug)
        skills = load_all()

        async with AsyncExitStack() as stack:
            mcp = await stack.enter_async_context(
                CustodianMcpClient(
                    endpoint=settings.cockroach_mcp_url,
                    api_key=settings.cockroach_service_account_key,
                    cluster_id=settings.cockroach_cluster_id,
                )
            )
            cloud_api = await stack.enter_async_context(
                CloudApiClient(
                    api_key=settings.cockroach_service_account_key,
                    cluster_id=settings.cockroach_cluster_id,
                )
            )

            # The deployed API's own MCP endpoint, write-scoped — the same
            # `mcp.Client` + `streamable_http_client` shape mcp_client.py
            # uses for the Cloud MCP connection, just a different server and
            # a plain Bearer token (no mcp-cluster-id header needed here).
            api_http_client = httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {settings.mnemos_api_key}"}
            )
            api_transport = streamable_http_client(
                f"{settings.mnemos_api_url}/mcp", http_client=api_http_client
            )
            api_mcp_client = await stack.enter_async_context(Client(api_transport))
            fact_writer = McpFactWriter(api_mcp_client)

            chat = OpenAIChatClient(api_key=settings.openai_api_key, model=settings.openai_model)

            log.info(
                "Custodian runtime ready",
                extra={"tenant_id": str(tenant_id), "skills": sorted(skills)},
            )
            yield CustodianRuntime(
                db=db,
                mcp=mcp,
                cloud_api=cloud_api,
                chat=chat,
                fact_writer=fact_writer,
                tenant_id=tenant_id,
                skills=skills,
            )
    finally:
        await db.close()


__all__ = ["CustodianRuntime", "CustodianSettings", "build_runtime", "load_settings"]
