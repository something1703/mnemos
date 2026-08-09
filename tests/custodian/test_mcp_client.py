"""`CustodianMcpClient` against a real in-process `MCPServer`, not a
hand-mocked stand-in — a fake server can advertise the exact tool names a
real CockroachDB Cloud MCP session would, including a deliberately
write-capable one, and `mcp.Client`/`mcp.server.mcpserver.MCPServer`'s real
wire format runs underneath. `mnemos_custodian.mcp_client.CustodianMcpClient`
accepts a `server=` override for exactly this — see its own docstring for
why real credentials still need `tests/custodian/test_mcp_client_live.py`
(not yet written; blocked on the Custodian's service account key)."""

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
    server = _read_only_server()

    @server.tool(name="create_table")
    def create_table(database: str, ddl: str) -> str:
        return "created"  # pragma: no cover — must never actually be reached

    return server


async def test_hard_fails_when_a_write_capable_tool_is_reachable() -> None:
    server = _server_exposing_a_write_tool()
    client = CustodianMcpClient(server)
    with pytest.raises(ReadOnlyGuaranteeViolated, match="create_table"):
        async with client:
            pass  # pragma: no cover — __aenter__ must raise before this runs


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
