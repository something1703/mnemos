"""`check_backup_recency` — pure decision logic, no network, no database.
The live round-trip (`CloudApiClient` + `backup_recency_finding` against the
real CockroachDB Cloud REST API) is `tests/custodian/test_cloud_api_live.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mnemos_custodian.cloud_api import STALE_BACKUP_GRACE_MULTIPLIER, check_backup_recency
from mnemos_custodian.findings import Severity, ToolSource

NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)


def _config(*, enabled: bool = True, frequency_minutes: int = 1440, retention_days: int = 30):
    return {
        "enabled": enabled,
        "frequency_minutes": frequency_minutes,
        "retention_days": retention_days,
    }


def _backup(hours_ago: float):
    as_of = NOW - timedelta(hours=hours_ago)
    return {"id": "abc", "as_of_time": as_of.strftime("%Y-%m-%dT%H:%M:%SZ")}


def test_disabled_backups_is_a_critical_finding() -> None:
    finding = check_backup_recency(_config(enabled=False), _backup(1), now=NOW)
    assert finding is not None
    assert finding.severity == Severity.CRITICAL
    assert "disabled" in finding.summary.lower()
    assert finding.tool_source == ToolSource.CCLOUD
    assert finding.skill_id == "reviewing-cluster-health"


def test_no_backup_at_all_is_a_critical_finding() -> None:
    finding = check_backup_recency(_config(), None, now=NOW)
    assert finding is not None
    assert finding.severity == Severity.CRITICAL
    assert "no backups found" in finding.summary.lower()


def test_recent_backup_within_the_configured_frequency_is_healthy() -> None:
    # 1440-minute (24h) frequency, backup 2 hours old — well within it.
    finding = check_backup_recency(_config(frequency_minutes=1440), _backup(2), now=NOW)
    assert finding is None


def test_backup_within_the_grace_multiplier_is_still_healthy() -> None:
    # 24h frequency, backup 47h old — just under the 2x (48h) grace window.
    finding = check_backup_recency(_config(frequency_minutes=1440), _backup(47), now=NOW)
    assert finding is None


def test_backup_beyond_the_grace_multiplier_is_a_critical_finding() -> None:
    # 24h frequency, backup 50h old — past the 2x (48h) grace window.
    finding = check_backup_recency(_config(frequency_minutes=1440), _backup(50), now=NOW)
    assert finding is not None
    assert finding.severity == Severity.CRITICAL
    assert "exceeding the configured rpo" in finding.summary.lower()
    assert finding.evidence["latest_backup"]["id"] == "abc"


def test_grace_multiplier_is_exactly_two() -> None:
    """Documents the exact threshold rather than just asserting behavior at
    arbitrary points — a change to STALE_BACKUP_GRACE_MULTIPLIER should be a
    deliberate edit to this test, not a silent behavior change."""
    assert STALE_BACKUP_GRACE_MULTIPLIER == 2
