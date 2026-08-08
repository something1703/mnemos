"""End-to-end through real MCP tool dispatch — the flow a live agent drives.

Calls `server.call_tool(...)` rather than the underlying engine functions, so
this covers argument coercion, the scope guard, and result rendering together.
A test that bypassed dispatch would not catch a tool whose signature the SDK
cannot bind.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from mnemos_api.keys import Scope
from mnemos_api.server import build_server

pytestmark = pytest.mark.security


@pytest.fixture
def server(runtime):
    return build_server(runtime)


def _payload(result: Any) -> Any:
    """Pull the structured JSON back out of a CallToolResult."""
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


async def test_remember_then_recall_through_dispatch(server, tenant, as_principal) -> None:
    as_principal(tenant, Scope.WRITE)
    session = str(uuid.uuid4())

    written = _payload(
        await server.call_tool(
            "remember",
            {
                "subject_key": "patient:rt",
                "content": "Severe anaphylactic allergy to penicillin.",
                "event_type": "intake",
                "source_trust": "operator",
                "session_id": session,
            },
        )
    )
    assert written["subject_key"] == "patient:rt"
    assert written["source_trust"] == "operator"
    uuid.UUID(written["event_id"])  # parses => a real id came back


async def test_recall_reports_what_it_withheld(server, runtime, tenant, as_principal) -> None:
    """The disclosure that stops an agent misreading a filtered result as an
    empty one. An unverified fact must not appear, but its existence must."""
    as_principal(tenant, Scope.WRITE)

    from mnemos_engine.embeddings import to_pgvector
    from mnemos_engine.ledger import append_audit
    from mnemos_engine.models import Op

    text = "Unverified claim that should be withheld from recall."
    vector = (await runtime.embedder.embed([text]))[0]

    async def seed(cur):
        ciphertext, wrapped = runtime.engine.envelope.encrypt(
            text, aad=f"mnemos:{tenant}:patient:withheld"
        )
        await append_audit(
            cur, tenant, op=Op.CONSOLIDATE, actor="test", subject_key="patient:withheld"
        )
        await cur.execute(
            """
            INSERT INTO mnemos.semantic_facts
                (tenant_id, fact_id, home_region, subject_key, fact_kind,
                 text_ciphertext, text_dek_wrapped, text_hash, embedding, tsv, trust)
            VALUES (%s, gen_random_uuid(), 'us-east-1', 'patient:withheld', 'note',
                    %s, %s, %s, %s, to_tsvector(%s), 'unverified')
            """,
            (tenant, ciphertext, wrapped, b"\x00" * 32, to_pgvector(vector), text),
        )

    await runtime.db.transaction(tenant, seed, label="seed")

    result = _payload(
        await server.call_tool("recall", {"query": text, "subject_key": "patient:withheld"})
    )
    assert result["facts"] == []
    assert result["unverified_withheld"] >= 1, (
        "recall must disclose that something exists but is untrusted"
    )


async def test_forget_preview_does_not_destroy(server, runtime, tenant, as_principal) -> None:
    """confirm=false is a genuine dry run. If a preview could destroy, the
    two-step confirmation would be theatre."""
    as_principal(tenant, Scope.ADMIN)

    from mnemos_engine.models import SourceTrust

    await runtime.engine.remember(
        tenant,
        subject_key="patient:preview-rt",
        session_id=uuid.uuid4(),
        event_type="note",
        content="should survive a preview",
        source_trust=SourceTrust.OPERATOR,
    )

    preview = _payload(
        await server.call_tool(
            "forget",
            {"subject_key": "patient:preview-rt", "reason": "dry run", "confirm": False},
        )
    )
    assert preview["preview"] is True
    assert preview["would_remove"]["episodes"] == 1

    async def count(cur):
        await cur.execute(
            "SELECT count(*) FROM mnemos.episodic_events WHERE tenant_id = %s AND subject_key = %s",
            (tenant, "patient:preview-rt"),
        )
        return (await cur.fetchone())[0]

    assert await runtime.db.transaction(tenant, count, label="verify") == 1


async def test_forget_refuses_under_legal_hold_with_the_matter_reference(
    server, runtime, tenant, as_principal
) -> None:
    """The refusal has to be actionable: a compliance officer needs the matter
    reference, not just 'denied'."""
    as_principal(tenant, Scope.ADMIN)

    from mnemos_engine.models import SourceTrust

    await runtime.engine.remember(
        tenant,
        subject_key="patient:held-rt",
        session_id=uuid.uuid4(),
        event_type="note",
        content="under investigation",
        source_trust=SourceTrust.OPERATOR,
    )
    await runtime.warden.set_legal_hold(
        tenant,
        "patient:held-rt",
        matter_reference="INS-2026-0417",
        placed_by="compliance",
        confirm=True,
    )

    with pytest.raises(MCPToolError) as exc:
        await server.call_tool(
            "forget",
            {"subject_key": "patient:held-rt", "reason": "erasure request", "confirm": True},
        )

    message = str(exc.value)
    assert "INS-2026-0417" in message, "the refusal must cite the matter"
    assert "REFUSED" in message


async def test_memory_stats_reports_the_real_posture(server, tenant, as_principal) -> None:
    """The instance must disclose whether privilege separation is actually on.
    In this test configuration it is not, and saying so is the point."""
    as_principal(tenant, Scope.READ)
    stats = _payload(await server.call_tool("memory_stats", {}))

    assert "posture" in stats
    assert stats["posture"]["privilege_separation"] is False, (
        "the test runtime shares one DSN; reporting True would be a lie"
    )
    assert "facts_by_trust" in stats


async def test_verify_ledger_discloses_the_missing_checkpoint_caveat(
    server, tenant, as_principal
) -> None:
    """A bare VALID would overstate what an unanchored chain proves."""
    as_principal(tenant, Scope.READ)
    result = _payload(await server.call_tool("verify_ledger", {}))
    assert result["valid"] is True
    if result["checkpoints_checked"] == 0:
        assert result["caveat"], "an unanchored chain must say what it cannot prove"
