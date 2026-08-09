"""Control-plane facts from the CockroachDB Cloud REST API — PHASE_07 7.5's
goal ("shell out to `ccloud` ... for control-plane facts the MCP server
cannot provide: cluster inventory, region topology, backup inventory and
recency"), reached by a different transport than the phase doc originally
specified.

**Why the REST API instead of the `ccloud` CLI, stated plainly.** The
`ccloud` binary (v0.8.23, installed and inspected 2026-08-09) has exactly
one authentication path: interactive OAuth (`ccloud auth login` opens a
browser, a human pastes back an authorization code). There is no
service-account/API-key-based non-interactive login — confirmed via
`--help` on every subcommand, environment-variable probing, and inspecting
the binary's own strings, which show only a browser-login code path
(`ServeBrowserLoginServer`, `requestToken`). A Fargate task has no browser
and no human present at 3am when a scheduled sweep runs; this CLI cannot
authenticate there as shipped.

The CLI itself is a thin wrapper over the same CockroachDB Cloud REST API
(`CCAPI`) this module calls directly, confirmed from its own OpenAPI spec
(`https://cockroachlabs.cloud/assets/docs/api/latest/openapi.json`), which
declares simple Bearer-token auth (`components.securitySchemes.Bearer`) —
the exact same service-account key already used for the Cloud MCP server.
Same data, same read-only-by-application-convention account, same
deployability story as `mcp_client.py`. `docs/limits.md`'s "ccloud CLI
cannot run non-interactively" section has the full account; `tool_source`
on `custodian_findings` still records these findings as `'ccloud'`
(migration 008's own naming — "control-plane facts distinct from the MCP
server's SQL-shaped ones" — not "produced by the literal ccloud binary").

Region topology and cluster inventory are already covered by
`mcp_client.py`'s `get_cluster`/`list_clusters` tools (confirmed live to
return the same `regions` data this API's `/clusters/{id}` endpoint would).
This module's actual new contribution is backup recency — the one PHASE_07
7.5 names as a concrete, worked example: "if the latest backup is older
than the tenant's declared RPO, that is a critical finding... A memory
system that notices its own backups are stale is a memory system that has
thought about failure."
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2

from .findings import FindingDraft, Severity, ToolSource

DEFAULT_BASE_URL = "https://cockroachlabs.cloud"
DEFAULT_TIMEOUT_SECONDS = 20.0
"""The backups list endpoint measured slow (>10s) against the real `mnemos`
cluster on at least one call — generous on purpose rather than tuned to the
one measurement, since a sweep runs on a schedule, not on a request a human
is waiting on."""

STALE_BACKUP_GRACE_MULTIPLIER = 2
"""A backup up to this many multiples of the configured frequency old is
ordinary jitter (a delayed run, one retry); beyond that is the finding."""


class CloudApiClient:
    """Thin async wrapper over the two endpoints this module needs. Not a
    general-purpose Cloud API client — `mnemos_custodian` has no use for
    the rest of the API's surface (cluster creation, IAM management, ...),
    and building one would be scope this project does not need."""

    def __init__(
        self,
        *,
        api_key: str,
        cluster_id: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._cluster_id = cluster_id
        self._client = httpx2.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def __aenter__(self) -> CloudApiClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def backup_config(self) -> dict[str, Any]:
        response = await self._client.get(f"/api/v1/clusters/{self._cluster_id}/backups-config")
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def latest_backup(self) -> dict[str, Any] | None:
        response = await self._client.get(
            f"/api/v1/clusters/{self._cluster_id}/backups",
            params={"pagination.limit": 1, "pagination.sort_order": "DESC"},
        )
        response.raise_for_status()
        backups = response.json().get("backups", [])
        result: dict[str, Any] | None = backups[0] if backups else None
        return result


def check_backup_recency(
    config: dict[str, Any], latest: dict[str, Any] | None, *, now: datetime
) -> FindingDraft | None:
    """Pure function — no network, no database — so the decision logic is
    testable against synthetic config/backup data without a live cluster.

    Returns `None` when backup posture is healthy, matching the sweep
    loop's own convention: a sweep with nothing to report is a normal,
    healthy outcome, not a failure to find something.
    """
    if not config.get("enabled"):
        return FindingDraft(
            severity=Severity.CRITICAL,
            summary="Backups are disabled for this cluster.",
            evidence={"backup_config": config},
            skill_id="reviewing-cluster-health",
            tool_source=ToolSource.CCLOUD,
            recommendation="Enable scheduled backups immediately.",
        )

    frequency_minutes = config["frequency_minutes"]
    threshold = timedelta(minutes=frequency_minutes) * STALE_BACKUP_GRACE_MULTIPLIER

    if latest is None:
        return FindingDraft(
            severity=Severity.CRITICAL,
            summary="No backups found for this cluster, despite backups being enabled.",
            evidence={"backup_config": config},
            skill_id="reviewing-cluster-health",
            tool_source=ToolSource.CCLOUD,
            recommendation="Investigate why no backup has ever completed.",
        )

    as_of = datetime.fromisoformat(str(latest["as_of_time"]).replace("Z", "+00:00"))
    age = now - as_of
    if age > threshold:
        return FindingDraft(
            severity=Severity.CRITICAL,
            summary=(
                f"Latest backup is {age.total_seconds() / 3600:.1f}h old, exceeding the "
                f"configured RPO of {frequency_minutes}m by more than "
                f"{STALE_BACKUP_GRACE_MULTIPLIER}x."
            ),
            evidence={"latest_backup": latest, "backup_config": config},
            skill_id="reviewing-cluster-health",
            tool_source=ToolSource.CCLOUD,
            recommendation="Investigate why scheduled backups have stopped running.",
        )
    return None


async def backup_recency_finding(client: CloudApiClient) -> FindingDraft | None:
    """The live round-trip: fetch config + latest backup, then apply
    `check_backup_recency`'s pure decision — split apart so the decision
    itself is unit-testable without touching the network."""
    config = await client.backup_config()
    latest = await client.latest_backup()
    return check_backup_recency(config, latest, now=datetime.now(UTC))


__all__ = [
    "DEFAULT_BASE_URL",
    "STALE_BACKUP_GRACE_MULTIPLIER",
    "CloudApiClient",
    "backup_recency_finding",
    "check_backup_recency",
]
