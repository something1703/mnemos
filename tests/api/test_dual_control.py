"""Dual control through real MCP tool dispatch — proving `forget` and
`revoke_source` actually pass `admin_key_id`/`admin_label` into the Warden,
not just that `mnemos_warden.approvals.enforce` works in isolation
(`tests/warden/test_approvals.py` covers that). A first admin's call must be
refused with nothing destroyed; a second, DISTINCT admin key against the
identical target is what lets execution through.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from mnemos_api.keys import Scope
from mnemos_api.server import build_server
from mnemos_engine.models import SourceTrust

pytestmark = pytest.mark.security


@pytest.fixture
def server(runtime):
    return build_server(runtime)


def _payload(result: Any) -> Any:
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return None


async def _enable_dual_control(runtime, tenant_id: uuid.UUID) -> None:
    async def run(cur):
        await cur.execute(
            "UPDATE mnemos.tenants SET dual_control = true WHERE tenant_id = %s", (tenant_id,)
        )

    await runtime.db.transaction(tenant_id, run, label="enable_dual_control")


async def test_forget_through_dispatch_needs_a_second_distinct_admin(
    server, runtime, tenant, as_principal
) -> None:
    await _enable_dual_control(runtime, tenant)
    subject = "patient:dual-control-rt"
    as_principal(tenant, Scope.WRITE)
    await runtime.engine.remember(
        tenant,
        subject_key=subject,
        session_id=uuid.uuid4(),
        event_type="note",
        content="must survive a single admin's approval",
        source_trust=SourceTrust.OPERATOR,
    )

    as_principal(tenant, Scope.ADMIN, "first-admin")
    with pytest.raises(MCPToolError) as exc:
        await server.call_tool(
            "forget", {"subject_key": subject, "reason": "poisoned", "confirm": True}
        )
    assert "DUAL CONTROL" in str(exc.value)

    async def count(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.episodic_events WHERE tenant_id = %s AND subject_key = %s",
            (tenant, subject),
        )
        return (await cur.fetchone())[0]

    assert await runtime.db.transaction(tenant, count, label="verify_untouched") == 1

    as_principal(tenant, Scope.ADMIN, "second-admin")
    executed = _payload(
        await server.call_tool(
            "forget", {"subject_key": subject, "reason": "poisoned", "confirm": True}
        )
    )
    assert executed["executed"] is True

    assert await runtime.db.transaction(tenant, count, label="verify_erased") == 0


async def test_forget_same_admin_calling_twice_stays_refused(
    server, runtime, tenant, as_principal
) -> None:
    await _enable_dual_control(runtime, tenant)
    subject = "patient:dual-control-same-admin"
    as_principal(tenant, Scope.WRITE)
    await runtime.engine.remember(
        tenant,
        subject_key=subject,
        session_id=uuid.uuid4(),
        event_type="note",
        content="one admin alone must never be enough",
        source_trust=SourceTrust.OPERATOR,
    )

    admin = as_principal(tenant, Scope.ADMIN, "only-admin")
    for _ in range(2):
        with pytest.raises(MCPToolError) as exc:
            await server.call_tool(
                "forget", {"subject_key": subject, "reason": "poisoned", "confirm": True}
            )
        assert "DUAL CONTROL" in str(exc.value)
        assert admin.label in str(exc.value)

    async def count(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.episodic_events WHERE tenant_id = %s AND subject_key = %s",
            (tenant, subject),
        )
        return (await cur.fetchone())[0]

    assert await runtime.db.transaction(tenant, count, label="verify_untouched") == 1


async def test_revoke_source_through_dispatch_needs_a_second_distinct_admin(
    server, runtime, tenant, as_principal
) -> None:
    await _enable_dual_control(runtime, tenant)
    as_principal(tenant, Scope.WRITE)
    episode = await runtime.engine.remember(
        tenant,
        subject_key="service:dual-control",
        session_id=uuid.uuid4(),
        event_type="postmortem",
        content="Poisoned remediation advice.",
        source_trust=SourceTrust.EXTERNAL,
    )

    as_principal(tenant, Scope.ADMIN, "first-admin")
    with pytest.raises(MCPToolError) as exc:
        await server.call_tool(
            "revoke_source",
            {
                "source_event_ids": [str(episode.event_id)],
                "reason": "confirmed poisoning",
                "confirm": True,
            },
        )
    assert "DUAL CONTROL" in str(exc.value)

    as_principal(tenant, Scope.ADMIN, "second-admin")
    executed = _payload(
        await server.call_tool(
            "revoke_source",
            {
                "source_event_ids": [str(episode.event_id)],
                "reason": "confirmed poisoning",
                "confirm": True,
            },
        )
    )
    assert executed["executed"] is True


async def test_set_legal_hold_through_dispatch_needs_a_second_distinct_admin(
    server, runtime, tenant, as_principal
) -> None:
    await _enable_dual_control(runtime, tenant)

    as_principal(tenant, Scope.ADMIN, "first-admin")
    with pytest.raises(MCPToolError) as exc:
        await server.call_tool(
            "set_legal_hold",
            {
                "subject_key": "patient:dual-control-hold",
                "matter_reference": "INS-2026-0900",
                "confirm": True,
            },
        )
    assert "DUAL CONTROL" in str(exc.value)

    as_principal(tenant, Scope.ADMIN, "second-admin")
    result = _payload(
        await server.call_tool(
            "set_legal_hold",
            {
                "subject_key": "patient:dual-control-hold",
                "matter_reference": "INS-2026-0900",
                "confirm": True,
            },
        )
    )
    assert result["active"] is True
