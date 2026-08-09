"""`CustodianMcpClient` against the real CockroachDB Cloud MCP server, using
the Custodian's actual service account — not a fake, not a mock.

Skipped unless `COCKROACH_MCP_URL`, `COCKROACH_SERVICE_ACCOUNT_KEY_CUSTODIAN`,
and `COCKROACH_MCP_CLUSTER_ID` are all set, matching the skip-on-unavailable
convention every other cluster-backed test in this project uses (e.g.
`tests/warden/conftest.py`'s `db` fixture) rather than failing CI runs that
have no cloud credentials configured.

**Deliberately does not call `create_database` here, or anywhere in this
test suite.** `mcp_client.py`'s module docstring records the one-time,
hand-verified finding that made this file's design what it is: no Cloud IAM
role short of Cluster Admin unlocks the MCP server's SQL tools at all
(confirmed against Cluster Monitor and Cluster Developer, both of which
blocked every SQL-shaped tool outright), and under Admin `create_database`
succeeds for real — it created a genuine scratch database
(`mnemos_readonly_probe_admin_check`) that had to be dropped by hand,
because this client holds no DROP capability. Re-running that probe on every
test run would keep creating scratch databases nothing ever cleans up. The
finding is recorded once, not re-demonstrated destructively.
"""

from __future__ import annotations

import os

import pytest
from mnemos_custodian.mcp_client import CustodianMcpClient

pytestmark = pytest.mark.cloud


def _live_credentials() -> tuple[str, str, str] | None:
    endpoint = os.environ.get("COCKROACH_MCP_URL")
    api_key = os.environ.get("COCKROACH_SERVICE_ACCOUNT_KEY_CUSTODIAN")
    cluster_id = os.environ.get("COCKROACH_MCP_CLUSTER_ID")
    if not (endpoint and api_key and cluster_id):
        return None
    return endpoint, api_key, cluster_id


@pytest.fixture
def live_client() -> CustodianMcpClient:
    creds = _live_credentials()
    if creds is None:
        pytest.skip(
            "no live Cloud MCP credentials (COCKROACH_MCP_URL / "
            "COCKROACH_SERVICE_ACCOUNT_KEY_CUSTODIAN / COCKROACH_MCP_CLUSTER_ID). "
            "Set them in .env to run this test."
        )
    endpoint, api_key, cluster_id = creds
    return CustodianMcpClient(endpoint=endpoint, api_key=api_key, cluster_id=cluster_id)


async def test_connects_and_lists_the_real_tool_catalog(live_client: CustodianMcpClient) -> None:
    async with live_client as client:
        assert client._tool_names, "the real server returned an empty tool catalog"


async def test_list_databases_returns_real_data(live_client: CustodianMcpClient) -> None:
    async with live_client as client:
        result = await client.call_tool("reviewing-cluster-health", "list_databases", {})
        assert result.is_error is False
        # Some Cloud MCP tools answer with structured_content, others only
        # with a text content block (see sweep.py's _render_tool_result,
        # which already handles both) — this just needs *a* real answer.
        assert result.structured_content is not None or result.content


async def test_show_running_queries_returns_real_data(live_client: CustodianMcpClient) -> None:
    async with live_client as client:
        result = await client.call_tool("triaging-live-sql-activity", "show_running_queries", {})
        assert result.is_error is False


async def test_call_tool_still_refuses_a_write_tool_against_the_real_server(
    live_client: CustodianMcpClient,
) -> None:
    """The one live-server assertion this suite makes about writes: that
    `call_tool()`'s backstop fires before any network call happens at all —
    proving this without ever actually attempting create_database against
    the real cluster."""
    from mnemos_custodian.mcp_client import ReadOnlyGuaranteeViolated

    async with live_client as client:
        with pytest.raises(ReadOnlyGuaranteeViolated):
            await client.call_tool(
                "reviewing-cluster-health", "create_database", {"database": "should-never-run"}
            )
