"""`CustodianMcpClient` against a real in-process `MCPServer`, not a
hand-mocked stand-in — a fake server can advertise the exact tool names a
real CockroachDB Cloud MCP session would, and `mcp.Client`/
`mcp.server.mcpserver.MCPServer`'s real wire format runs underneath.
`mnemos_custodian.mcp_client.CustodianMcpClient` accepts a `server=`
override for exactly this. `tests/custodian/test_mcp_client_live.py`
(`@pytest.mark.cloud`) is the real-credentials counterpart — this file
proves the logic in isolation; that one proves it against the actual
CockroachDB Cloud MCP server."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver import MCPServer
from mnemos_custodian.mcp_client import (
    CustodianMcpClient,
    ReadOnlyGuaranteeViolated,
    ToolNotAllowlisted,
)

pytestmark = pytest.mark.security


def _read_only_server() -> MCPServer:
    server = MCPServer("fake-cloud-mcp-read-only")

    @server.tool(name="show_running_queries")
    def show_running_queries() -> list[dict]:
        return [{"query_id": "1", "query_preview": "SELECT 1"}]

    @server.tool(name="list_tables")
    def list_tables(database: str) -> list[str]:
        return ["episodic_events", "semantic_facts"]

    return server


def _server_exposing_a_write_tool() -> MCPServer:
    """Reproduces the real, confirmed shape of the Custodian's actual
    credential (see mcp_client.py's module docstring): create_database is
    listed AND a real call against it succeeds. Connecting must still
    succeed — the safety boundary is call_tool()'s refusal to invoke it,
    not the connection itself."""
    server = _read_only_server()

    @server.tool(name="create_database")
    def create_database(database: str) -> str:
        return "created"  # pragma: no cover — call_tool() must never reach this

    return server


async def test_connects_even_when_a_write_tool_is_reachable() -> None:
    """No live write probe on connect (see module docstring for why: it
    would create a real, uncleanable scratch database on every startup
    against the Custodian's actual credential) — connecting only inventories
    the catalog."""
    server = _server_exposing_a_write_tool()
    async with CustodianMcpClient(server) as client:
        assert client._tool_names == {"show_running_queries", "list_tables", "create_database"}


async def test_call_tool_refuses_a_write_capable_tool_even_if_somehow_requested() -> None:
    """The backstop: call_tool() refuses create_database by name, before
    even checking whether any skill's allowlist entry mentions it — the
    ReadOnlyGuaranteeViolated backstop, independent of allowlist.py."""
    server = _server_exposing_a_write_tool()
    async with CustodianMcpClient(server) as client:
        with pytest.raises(ReadOnlyGuaranteeViolated, match="write-capable"):
            await client.call_tool(
                "triaging-live-sql-activity", "create_database", {"database": "x"}
            )


async def test_connects_when_only_read_only_tools_are_reachable() -> None:
    server = _read_only_server()
    async with CustodianMcpClient(server) as client:
        assert client._tool_names == {"show_running_queries", "list_tables"}


async def test_call_tool_allowed_for_a_mapped_skill_and_tool() -> None:
    server = _read_only_server()
    async with CustodianMcpClient(server) as client:
        result = await client.call_tool("triaging-live-sql-activity", "show_running_queries", {})
        assert result.is_error is False


async def test_call_tool_rejected_when_skill_does_not_allowlist_the_tool() -> None:
    """triaging-live-sql-activity's allowlist does not include list_tables —
    even though list_tables is present and read-only, this skill may not
    call it."""
    server = _read_only_server()
    async with CustodianMcpClient(server) as client:
        with pytest.raises(ToolNotAllowlisted, match="not allowlisted"):
            await client.call_tool("triaging-live-sql-activity", "list_tables", {"database": "x"})


async def test_call_tool_rejected_for_an_unknown_skill() -> None:
    server = _read_only_server()
    async with CustodianMcpClient(server) as client:
        with pytest.raises(ToolNotAllowlisted, match="not allowlisted"):
            await client.call_tool("no-such-skill", "show_running_queries", {})


async def test_call_tool_rejected_when_allowlisted_but_not_actually_present() -> None:
    """cockroachdb-sql's allowlist includes get_table_schema, but this fake
    server never registered it — the live-catalog check must catch a
    drifted allowlist, not just an unlisted skill."""
    server = _read_only_server()
    async with CustodianMcpClient(server) as client:
        with pytest.raises(ToolNotAllowlisted, match="live tool catalog"):
            await client.call_tool(
                "cockroachdb-sql", "get_table_schema", {"database": "x", "table": "y"}
            )


def test_requires_credentials_when_no_server_override_is_given() -> None:
    with pytest.raises(ValueError, match="api_key and cluster_id are required"):
        CustodianMcpClient()
