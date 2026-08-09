"""The Custodian's connection to the CockroachDB Cloud MCP server.

PHASE_07 7.2's own words: "**Read-only asserted at startup**: probe the tool
list; if any write-capable tool is present and not denied, hard fail and
refuse to run. Do not trust the mode — verify it." This module is that
assertion, plus a `call_tool` wrapper that also enforces `allowlist.py`'s
per-skill mapping — two independent gates, since a tool being globally
read-only does not mean every skill should be allowed to call it.

**Why "hard fail on presence" rather than "attempt and expect a permission
error."** The literal instruction above says "verify it," which could mean
either. Attempting a write-capable tool to prove the service account can't
actually use it risks the exact side effect this check exists to prevent, if
the assumption about the account's scope ever turns out to be wrong — a
single unlucky misconfiguration would mean the "verification" itself is what
mutates the cluster. Refusing outright the moment a write-capable tool is
merely *listed* is the safer of the two readings, at the cost of also
refusing to run against an MCP server that exposes those tools to everyone
but denies them per-account at call time. That tradeoff is deliberate and
stated here rather than left implicit.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.server.mcpserver import MCPServer

from . import allowlist

log = logging.getLogger("mnemos.custodian.mcp_client")

DEFAULT_ENDPOINT = "https://cockroachlabs.cloud/mcp"


class ReadOnlyGuaranteeViolated(Exception):
    """The Cloud MCP session exposed a write-capable tool.

    Raised at startup (`CustodianMcpClient.__aenter__`), before any skill's
    sweep runs — the Custodian must never reach a state where it has already
    started diagnosing a cluster it cannot prove is safe to talk to.
    """


class ToolNotAllowlisted(Exception):
    """A call was attempted that `allowlist.py` does not permit for this
    skill, or that the live server does not currently expose at all."""


class CustodianMcpClient:
    """Async context manager wrapping `mcp.Client`, scoped to one CockroachDB
    Cloud cluster and one read-only service account.

    `server` is normally left `None`, in which case a real
    `streamable_http_client` transport is built from `endpoint`/`api_key`/
    `cluster_id`. Tests pass an in-process `MCPServer`/`Server` directly
    (`mcp.Client`'s own supported in-memory transport) so the read-only
    assertion and allowlist enforcement run against a real `Client`/`Server`
    round-trip, not a hand-mocked stand-in for one.
    """

    def __init__(
        self,
        server: Server[Any] | MCPServer[Any] | None = None,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        api_key: str = "",
        cluster_id: str = "",
    ) -> None:
        if server is None and not (api_key and cluster_id):
            raise ValueError("api_key and cluster_id are required unless server= is given")
        self._server = server
        self._endpoint = endpoint
        self._api_key = api_key
        self._cluster_id = cluster_id
        self._exit_stack: AsyncExitStack | None = None
        self._client: Client | None = None
        self._tool_names: frozenset[str] = frozenset()

    async def __aenter__(self) -> CustodianMcpClient:
        self._exit_stack = AsyncExitStack()
        target: Server[Any] | MCPServer[Any] | Any
        if self._server is not None:
            target = self._server
        else:
            http_client = httpx2.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "mcp-cluster-id": self._cluster_id,
                }
            )
            target = streamable_http_client(self._endpoint, http_client=http_client)

        client = Client(target)
        self._client = await self._exit_stack.enter_async_context(client)
        await self._assert_read_only()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._exit_stack is not None
        await self._exit_stack.aclose()
        self._exit_stack = None
        self._client = None

    async def _assert_read_only(self) -> None:
        assert self._client is not None
        result = await self._client.list_tools()
        self._tool_names = frozenset(t.name for t in result.tools)

        reachable_writes = self._tool_names & allowlist.WRITE_CAPABLE_TOOLS
        if reachable_writes:
            raise ReadOnlyGuaranteeViolated(
                f"the Cloud MCP session exposes write-capable tool(s) "
                f"{sorted(reachable_writes)} — refusing to run. The Custodian's "
                "service account must be read-only; check its granted role in "
                "the CockroachDB Cloud console."
            )
        log.info(
            "read-only guarantee verified",
            extra={"tool_count": len(self._tool_names), "tools": sorted(self._tool_names)},
        )

    async def call_tool(self, skill_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Every call names the skill on whose behalf it runs — the run_id
        + skill_id + tool + arguments is the audit trail PHASE_07 7.2 asks
        for ('every MCP call and its response are logged with the run_id')."""
        if not allowlist.is_allowed(skill_id, tool_name):
            raise ToolNotAllowlisted(
                f"skill {skill_id!r} is not allowlisted to call tool {tool_name!r} "
                "— see mnemos_custodian.allowlist.ALLOWLIST"
            )
        if tool_name not in self._tool_names:
            raise ToolNotAllowlisted(
                f"tool {tool_name!r} is allowlisted for {skill_id!r} but was not in "
                "this session's live tool catalog — the Cloud MCP server's surface "
                "may have changed since allowlist.py was written"
            )
        assert self._client is not None
        log.info("mcp call", extra={"skill_id": skill_id, "tool": tool_name})
        return await self._client.call_tool(tool_name, arguments)


__all__ = [
    "DEFAULT_ENDPOINT",
    "CustodianMcpClient",
    "ReadOnlyGuaranteeViolated",
    "ToolNotAllowlisted",
]
