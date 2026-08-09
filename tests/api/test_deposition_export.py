"""`GET /v1/deposition/{action_id}/export.html` — the self-contained,
offline-verifying deposition artifact (PHASE_06 6.7), through a real ASGI
request rather than calling `render_deposition_html` directly. Nothing in
this project had exercised `services/api/src/mnemos_api/rest.py` over HTTP
before this file — a route that only ever gets called by hand-inspecting its
Python is a route whose auth wiring, path params, and response type have
never actually been proven.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from mnemos_api.rest import build_rest_app
from mnemos_engine.accountability import record_action
from mnemos_engine.ledger import checkpoint as take_checkpoint
from mnemos_engine.models import SourceTrust

pytestmark = pytest.mark.security


@pytest.fixture
async def client(runtime):
    app = build_rest_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _anchored_less_action(runtime, tenant: uuid.UUID) -> uuid.UUID:
    """An action with a real, checkpointed audit trail — unanchored, so this
    also proves the export degrades gracefully with no S3 anchor at all."""
    subject = "applicant:rest-export"
    session = uuid.uuid4()
    await runtime.engine.remember(
        tenant,
        subject_key=subject,
        session_id=session,
        event_type="record",
        content="Subject reports a prior address.",
        source_trust=SourceTrust.OPERATOR,
    )
    recalled = await runtime.engine.recall(
        tenant, "prior address", subject_key=subject, session_id=session
    )

    async def declare_and_checkpoint(cur):
        action_id = await record_action(
            cur,
            tenant,
            action_type="verify",
            description="Address verified.",
            recall_ids=recalled.recall_ids,
            actor="agent:reviewer",
            session_id=session,
            subject_key=subject,
        )
        await take_checkpoint(cur, tenant)
        return action_id

    return await runtime.db.transaction(tenant, declare_and_checkpoint, label="declare")


async def test_export_requires_authentication(client) -> None:
    response = await client.get(f"/v1/deposition/{uuid.uuid4()}/export.html")
    assert response.status_code == 401


async def test_export_404s_for_an_unknown_action(client, minted_keys) -> None:
    response = await client.get(
        f"/v1/deposition/{uuid.uuid4()}/export.html",
        headers={"Authorization": f"Bearer {minted_keys['read']}"},
    )
    assert response.status_code == 404


async def test_export_renders_a_self_contained_verifiable_html_page(
    client, runtime, tenant, minted_keys
) -> None:
    action_id = await _anchored_less_action(runtime, tenant)

    response = await client.get(
        f"/v1/deposition/{action_id}/export.html",
        headers={"Authorization": f"Bearer {minted_keys['read']}"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text

    # The page must be self-contained: no external script/link/CDN reference.
    assert "cdn." not in body
    assert '<link rel="stylesheet"' not in body
    assert 'src="http' not in body

    # It must embed real chain data for this action, not a stub.
    assert str(action_id) in body
    assert "verify-btn" in body
    assert '"chain_entries"' in body
    assert '"payload_hash"' in body
    assert "Not yet anchored" in body  # this action's checkpoint was never anchored

    # A stray `</script` inside the embedded JSON would prematurely close the
    # tag it's meant to be data inside of — every opening <script> must still
    # have exactly one matching close.
    assert body.count("<script>") == body.count("</script>")
