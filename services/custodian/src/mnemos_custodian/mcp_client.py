"""The Custodian's connection to the CockroachDB Cloud MCP server.

PHASE_07 7.2's own words: "**Read-only asserted at startup**: probe the tool
list; if any write-capable tool is present and not denied, hard fail and
refuse to run. Do not trust the mode — verify it." What "verify it" turned
out to mean, in practice, took two rounds of being wrong against the real
server — recorded here because the final design only makes sense in light
of both.

**Round 1 (rejected): hard-fail if a write-capable tool is merely listed.**
Tested against the real CockroachDB Cloud MCP server (2026-08-09, the
`mnemos` cluster): `list_tools()` returns the identical catalog — write
tools included — regardless of the calling account's actual privileges.
This server does not scope its advertised catalog per principal; enforcement
happens at execution time, in CockroachDB itself. Under that reality this
design could never succeed at all, for any account.

**Round 2 (rejected): verify by attempting `create_database` and requiring
it to fail.** This worked, once — genuinely confirmed a Cluster Monitor-
scoped account gets `unauthorized`. But the deeper problem surfaced next:
**no Cloud IAM role short of Cluster Admin unlocks the MCP server's SQL
tools at all.** Monitor and Developer both block `list_databases`/
`show_running_queries`/`list_tables` entirely — not just writes, ALL SQL
access. Only Admin works, and under Admin the write probe *succeeds*
(confirmed live: it created a real, harmless, but real scratch database —
`mnemos_readonly_probe_admin_check` — that had to be dropped by hand,
because this client holds no DROP capability by design). A startup check
that hard-fails whenever the probe succeeds would mean the Custodian could
never start at all, using the only credential that can see anything.

**What this means, stated plainly:** CockroachDB Cloud's service-account
roles do not currently offer a tier that is both SQL-capable and
platform-enforced read-only, for this MCP integration, on this cluster's
tier. The Custodian's actual credential is Cluster Admin — genuinely
capable of `create_database`/`create_table`/`insert_rows` if asked.

**So the real safety boundary is `allowlist.py`, enforced twice:** every
`call_tool()` requires the (skill, tool) pair to be in `allowlist.ALLOWLIST`
— which never contains a write-capable tool, for any skill, checked
statically by `tests/custodian/test_allowlist.py` — and, as a second,
independent backstop below, `call_tool()` also refuses outright to invoke
any of `allowlist.WRITE_CAPABLE_TOOLS` by name, regardless of what any
allowlist entry says. Two things would have to be wrong at once — a bad
`ALLOWLIST` entry *and* this backstop removed — for the Custodian's own
code to ever ask the account to write. This is a weaker guarantee than
"the account cannot write even if asked" (`docs/limits.md` says so,
un-euphemized), and a stronger one than "we trust it not to."
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
    """`call_tool()` was asked to invoke a write-capable tool.

    This is the backstop, not the primary gate — `allowlist.is_allowed()`
    already refuses this for every skill, checked statically. This exists
    for the same reason a second lock exists on a door the first lock
    already covers: one bad `ALLOWLIST` entry should not be the only thing
    standing between the Custodian and a write.
    """


class ToolNotAllowlisted(Exception):
    """A call was attempted that `allowlist.py` does not permit for this
    skill, or that the live server does not currently expose at all."""


class CustodianMcpClient:
    """Async context manager wrapping `mcp.Client`, scoped to one CockroachDB
    Cloud cluster and one service account.

    `server` is normally left `None`, in which case a real
    `streamable_http_client` transport is built from `endpoint`/`api_key`/
    `cluster_id`. Tests pass an in-process `MCPServer`/`Server` directly
    (`mcp.Client`'s own supported in-memory transport) so `call_tool()`'s
    enforcement runs against a real `Client`/`Server` round-trip, not a
    hand-mocked stand-in for one.
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
        await self._log_capabilities()
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

    async def _log_capabilities(self) -> None:
        """Informational only, deliberately — no live write probe here.

        Tested and confirmed reachable (see module docstring): the
        credential this client uses can call `create_database` and it
        succeeds. Probing that on every connection would create a fresh,
        uncleanable scratch database every time the Custodian starts up.
        The one-time, hand-verified finding is recorded in this module's
        docstring instead of re-demonstrated destructively on every run.
        """
        assert self._client is not None
        result = await self._client.list_tools()
        self._tool_names = frozenset(t.name for t in result.tools)

        reachable_writes = self._tool_names & allowlist.WRITE_CAPABLE_TOOLS
        if reachable_writes:
            log.warning(
                "Cloud MCP catalog lists write-capable tool(s) %s, reachable by this "
                "session's credential — the safety boundary is allowlist.py's "
                "per-skill mapping and call_tool()'s own refusal to invoke them, not "
                "the account's own privileges. See mcp_client.py's module docstring.",
                sorted(reachable_writes),
            )
        log.info("Cloud MCP session ready", extra={"tool_count": len(self._tool_names)})

    async def call_tool(self, skill_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Every call names the skill on whose behalf it runs — the run_id
        + skill_id + tool + arguments is the audit trail PHASE_07 7.2 asks
        for ('every MCP call and its response are logged with the run_id')."""
        if tool_name in allowlist.WRITE_CAPABLE_TOOLS:
            raise ReadOnlyGuaranteeViolated(
                f"refusing to call {tool_name!r} — it is write-capable "
                "(mnemos_custodian.allowlist.WRITE_CAPABLE_TOOLS) and the Custodian "
                "may never invoke a write tool regardless of what any allowlist "
                "entry says. This is the backstop; something upstream asked for "
                "this call at all, which is itself worth investigating."
            )
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
