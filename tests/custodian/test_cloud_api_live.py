"""`CloudApiClient` against the real CockroachDB Cloud REST API, using the
Custodian's actual service account — not a fake, not a mock.

Skipped unless `COCKROACH_SERVICE_ACCOUNT_KEY_CUSTODIAN` and
`COCKROACH_MCP_CLUSTER_ID` are both set, same convention as
`tests/custodian/test_mcp_client_live.py`.
"""

from __future__ import annotations

import os

import pytest
from mnemos_custodian.cloud_api import CloudApiClient, backup_recency_finding

pytestmark = pytest.mark.cloud


def _live_credentials() -> tuple[str, str] | None:
    api_key = os.environ.get("COCKROACH_SERVICE_ACCOUNT_KEY_CUSTODIAN")
    cluster_id = os.environ.get("COCKROACH_MCP_CLUSTER_ID")
    if not (api_key and cluster_id):
        return None
    return api_key, cluster_id


@pytest.fixture
def live_client() -> CloudApiClient:
    creds = _live_credentials()
    if creds is None:
        pytest.skip(
            "no live Cloud API credentials (COCKROACH_SERVICE_ACCOUNT_KEY_CUSTODIAN / "
            "COCKROACH_MCP_CLUSTER_ID). Set them in .env to run this test."
        )
    api_key, cluster_id = creds
    return CloudApiClient(api_key=api_key, cluster_id=cluster_id)


async def test_backup_config_returns_real_data(live_client: CloudApiClient) -> None:
    async with live_client as client:
        config = await client.backup_config()
        assert isinstance(config["enabled"], bool)
        assert isinstance(config["frequency_minutes"], int)
        assert isinstance(config["retention_days"], int)


async def test_latest_backup_returns_real_data_or_none(live_client: CloudApiClient) -> None:
    async with live_client as client:
        latest = await client.latest_backup()
        if latest is not None:
            assert "id" in latest
            assert "as_of_time" in latest


async def test_backup_recency_finding_round_trips_against_the_real_cluster(
    live_client: CloudApiClient,
) -> None:
    """Not asserting the finding is None or non-None — the `mnemos` cluster's
    actual backup health can change. This proves the round trip runs
    end-to-end against the real API without raising."""
    async with live_client as client:
        result = await backup_recency_finding(client)
        assert result is None or result.tool_source.value == "ccloud"
