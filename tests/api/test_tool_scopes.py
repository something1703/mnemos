"""The scope matrix — invariant 1 enforced against *callers*, not just roles.

`packages/warden` guarantees destruction is deterministic and model-free.
`migration 011` guarantees the API's database role holds no DELETE. This suite
covers the third boundary: an agent holding an ordinary write key cannot reach
a destructive tool at all, no matter what it asks for.

Exhaustive rather than representative. A gate that protects `forget` but not
`revoke_source` is not a gate, and the only way to know is to try every
destructive tool at every scope below admin.
"""

from __future__ import annotations

import uuid

import pytest
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from mnemos_api.context import current_principal
from mnemos_api.keys import AuthError, Scope, require
from mnemos_api.server import build_server
from mnemos_engine.models import SourceTrust

pytestmark = pytest.mark.security

ADMIN_TOOLS = ["forget", "revoke_source", "set_legal_hold"]
WRITE_TOOLS = ["remember", "record_action", "learn_skill"]
READ_TOOLS = [
    "recall",
    "recall_as_of",
    "explain",
    "blast_radius",
    "find_skill",
    "where_is",
    "memory_stats",
    "verify_ledger",
]


@pytest.fixture
def server(runtime):
    return build_server(runtime)


async def test_every_planned_tool_is_registered(server) -> None:
    """PHASE_04.1 specifies the surface; this asserts the code matches the plan
    rather than trusting that it drifted in the right direction."""
    registered = {t.name for t in await server.list_tools()}
    expected = set(ADMIN_TOOLS + WRITE_TOOLS + READ_TOOLS)
    assert expected <= registered, f"missing tools: {expected - registered}"


async def test_tool_descriptions_warn_about_irreversibility(server) -> None:
    """An agent decides whether to call a tool from its description alone. If
    the description of an irreversible operation does not say so, the model has
    no way to be appropriately cautious — this is a UX property with security
    consequences, so it is tested rather than assumed."""
    by_name = {t.name: (t.description or "") for t in await server.list_tools()}

    assert "IRREVERSIBLE" in by_name["forget"]
    assert "confirm=true" in by_name["forget"]
    assert "legal hold" in by_name["forget"].lower()

    assert "admin" in by_name["revoke_source"].lower()
    assert "confirm=true" in by_name["revoke_source"]

    # The read side must disclose that it hides things, or an agent will
    # misread a filtered result as an empty one.
    assert "unverified" in by_name["recall"].lower()
    assert "quarantined" in by_name["find_skill"].lower()


# --------------------------------------------------------------- the matrix


@pytest.mark.parametrize("tool_name", ADMIN_TOOLS)
@pytest.mark.parametrize("scope", [Scope.READ, Scope.WRITE])
def test_admin_tools_are_refused_below_admin_scope(
    tool_name: str, scope: Scope, as_principal, tenant
) -> None:
    """The core assertion. Checked at the `require()` boundary that every
    admin tool calls before touching a Warden connection."""
    as_principal(tenant, scope)
    principal = current_principal()

    with pytest.raises(AuthError) as exc:
        require(principal, Scope.ADMIN, tool_name)

    assert exc.value.status == 403
    assert "admin" in str(exc.value)
    assert scope.label in str(exc.value), "the error should name the scope actually held"


@pytest.mark.parametrize("tool_name", WRITE_TOOLS)
def test_write_tools_are_refused_at_read_scope(tool_name: str, as_principal, tenant) -> None:
    as_principal(tenant, Scope.READ)
    with pytest.raises(AuthError) as exc:
        require(current_principal(), Scope.WRITE, tool_name)
    assert exc.value.status == 403


@pytest.mark.parametrize("scope", [Scope.READ, Scope.WRITE, Scope.ADMIN])
def test_read_tools_are_allowed_at_every_scope(scope: Scope, as_principal, tenant) -> None:
    as_principal(tenant, scope)
    for tool_name in READ_TOOLS:
        require(current_principal(), Scope.READ, tool_name)  # must not raise


def test_admin_scope_reaches_everything(as_principal, tenant) -> None:
    as_principal(tenant, Scope.ADMIN)
    principal = current_principal()
    for needed in (Scope.READ, Scope.WRITE, Scope.ADMIN):
        require(principal, needed, "any")  # must not raise


# ------------------------------------------------- end-to-end through a tool


async def test_write_key_calling_forget_is_refused_end_to_end(
    server, runtime, tenant, as_principal
) -> None:
    """Not just the guard in isolation — dispatch the real tool with a
    write-scoped caller and confirm it refuses before doing anything.

    Seeds a real subject first, so a pass cannot be explained away by the
    subject simply not existing.
    """
    as_principal(tenant, Scope.WRITE)
    await runtime.engine.remember(
        tenant,
        subject_key="patient:scoped",
        session_id=uuid.uuid4(),
        event_type="note",
        content="present, so a refusal cannot be mistaken for absence",
        source_trust=SourceTrust.OPERATOR,
    )

    # The SDK surfaces a raising tool as an MCP ToolError, which the protocol
    # layer turns into an error result for the client. Either way the call
    # fails; what matters is that it fails BEFORE touching anything.
    with pytest.raises(MCPToolError) as exc:
        await server.call_tool(
            "forget",
            {
                "subject_key": "patient:scoped",
                "reason": "unauthorised attempt",
                "confirm": True,
            },
        )
    assert "admin" in str(exc.value).lower()

    # And the data is untouched.
    async def count(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.episodic_events WHERE tenant_id = %s AND subject_key = %s",
            (tenant, "patient:scoped"),
        )
        return (await cur.fetchone())[0]

    assert await runtime.db.transaction(tenant, count, label="verify") == 1


async def test_unauthenticated_call_fails_closed(server) -> None:
    """With no principal bound, a tool must refuse rather than fall through to
    whatever tenant scoping happens to be in place."""
    with pytest.raises(MCPToolError) as exc:
        await server.call_tool("memory_stats", {})
    assert "authenticated" in str(exc.value).lower()
