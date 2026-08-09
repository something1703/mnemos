"""recall() must actually enforce residency projection, not merely be able to
in a unit test. `packages/warden/src/mnemos_warden/residency.py` implements
`enforce_recall_projection` correctly and in isolation — the gap this file
regression-tests is that nothing in the request path ever called it. Before
`_apply_residency` existed in `tools.py`, a crafted `recall()` call returned
every matching fact's full text regardless of its home region: invariant 4's
read side was unit-tested capability, not enforced behaviour.

Every test here goes through real MCP tool dispatch (`server.call_tool`), the
same path `tests/api/test_tool_roundtrip.py` uses, for the same reason: a
test that called `enforce_recall_projection` directly would prove the
function works, not that `recall()` uses it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from mnemos_api.keys import Scope
from mnemos_api.server import build_server
from mnemos_engine.embeddings import to_pgvector
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Op

pytestmark = pytest.mark.security

REQUESTER_REGION = "us-east-1"  # matches tests/api/conftest.py's _settings()
FOREIGN_REGION = "eu-central-1"


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


async def _seed_policy(runtime, tenant_id: uuid.UUID, pattern: str, projection: str) -> None:
    async def run(cur):
        await append_audit(
            cur,
            tenant_id,
            op=Op.POLICY,
            actor="test",
            subject_key=pattern,
            payload={"projection": projection},
        )
        await cur.execute(
            "INSERT INTO mnemos.residency_policies "
            "(tenant_id, subject_pattern, home_region, projection) "
            "VALUES (%s, %s, %s, %s)",
            (tenant_id, pattern, FOREIGN_REGION, projection),
        )

    await runtime.db.transaction(tenant_id, run, label="seed_policy")


async def _seed_fact(
    runtime, tenant_id: uuid.UUID, subject_key: str, text: str, home_region: str
) -> None:
    vector = (await runtime.embedder.embed([text]))[0]
    fact_id = uuid.uuid4()
    event_id = uuid.uuid4()

    async def run(cur):
        ciphertext, wrapped = runtime.engine.envelope.encrypt(
            text, aad=f"mnemos:{tenant_id}:{subject_key}"
        )
        await append_audit(cur, tenant_id, op=Op.CONSOLIDATE, actor="test", subject_key=subject_key)
        # trust='trusted' requires a provenance edge already present —
        # migration 010's require_provenance trigger, same as every other
        # fixture in this codebase that seeds a directly-recallable fact.
        await cur.execute(
            "INSERT INTO mnemos.fact_provenance (tenant_id, fact_id, event_id, subject_key) "
            "VALUES (%s, %s, %s, %s)",
            (tenant_id, fact_id, event_id, subject_key),
        )
        await cur.execute(
            """
            INSERT INTO mnemos.semantic_facts
                (tenant_id, fact_id, home_region, subject_key, fact_kind,
                 text_ciphertext, text_dek_wrapped, text_hash, embedding, tsv, trust)
            VALUES (%s, %s, %s, %s, 'note', %s, %s, %s, %s, to_tsvector(%s), 'trusted')
            """,
            (
                tenant_id,
                fact_id,
                home_region,
                subject_key,
                ciphertext,
                wrapped,
                b"\x00" * 32,
                to_pgvector(vector),
                text,
            ),
        )

    await runtime.db.transaction(tenant_id, run, label="seed_fact")


async def test_none_projection_withholds_cross_border_content(
    server, runtime, tenant, as_principal
) -> None:
    as_principal(tenant, Scope.READ)
    subject = "patient:eu:residency-none"
    text = "A fact that must never leave eu-central-1."
    await _seed_policy(runtime, tenant, "patient:eu:*", "none")
    await _seed_fact(runtime, tenant, subject, text, FOREIGN_REGION)

    result = _payload(await server.call_tool("recall", {"query": text, "subject_key": subject}))

    assert result["facts"] == [], "a 'none' projection must never return the fact"
    assert result["residency_withheld"] >= 1, (
        "the caller must be told something was withheld, not shown a silent empty list"
    )


async def test_aggregate_projection_withholds_individual_facts(
    server, runtime, tenant, as_principal
) -> None:
    as_principal(tenant, Scope.READ)
    subject = "patient:eu:residency-aggregate"
    text = "A fact only servable as an aggregate statistic, not individually."
    await _seed_policy(runtime, tenant, "patient:eu:*", "aggregate")
    await _seed_fact(runtime, tenant, subject, text, FOREIGN_REGION)

    result = _payload(await server.call_tool("recall", {"query": text, "subject_key": subject}))

    assert result["facts"] == [], "aggregate-only policy must not return an individual fact"
    assert result["residency_withheld"] >= 1


async def test_derived_projection_serves_the_fact_and_logs_the_crossing(
    server, runtime, tenant, as_principal
) -> None:
    as_principal(tenant, Scope.READ)
    subject = "patient:eu:residency-derived"
    text = "A derived fact permitted to cross the border, unlike its source episode."
    await _seed_policy(runtime, tenant, "patient:eu:*", "derived")
    await _seed_fact(runtime, tenant, subject, text, FOREIGN_REGION)

    result = _payload(await server.call_tool("recall", {"query": text, "subject_key": subject}))

    assert len(result["facts"]) == 1, "a 'derived' policy must serve the fact"
    assert result["facts"][0]["text"] == text
    assert result["residency_withheld"] == 0

    async def count_crossings(cur):
        await cur.execute(
            "SELECT allowed FROM mnemos.region_crossings WHERE tenant_id = %s AND subject_key = %s",
            (tenant, subject),
        )
        return await cur.fetchall()

    crossings = await runtime.db.transaction(tenant, count_crossings, label="check", read_only=True)
    assert any(row[0] for row in crossings), "a permitted crossing must be logged, not silent"


async def test_same_region_fact_is_never_filtered_regardless_of_policy(
    server, runtime, tenant, as_principal
) -> None:
    """The policy governs crossings. A fact already homed in the requester's
    own region is not crossing anything, and must be served even under a
    'none' policy that would block a genuine border crossing."""
    as_principal(tenant, Scope.READ)
    subject = "staff:residency-same-region"
    text = "A fact already homed in the requester's own region."
    await _seed_policy(runtime, tenant, "staff:*", "none")
    await _seed_fact(runtime, tenant, subject, text, REQUESTER_REGION)

    result = _payload(await server.call_tool("recall", {"query": text, "subject_key": subject}))

    assert len(result["facts"]) == 1
    assert result["residency_withheld"] == 0
