"""`explain()` must hand a caller a way to verify the anchored root
themselves, not just print the `s3://` URI of a bucket they hold no
credential for.

`mnemos_warden.attestation.presign_anchor_url` and the CLI surface that calls
it are already covered (`tests/warden/test_attestation_mocked.py`,
`tests/warden/test_cli.py`) — the gap this file closes is that nothing in the
`explain` tool's own request path ever called it: a deposition for an
anchored action reported `anchor_uri` (a private bucket path a judge cannot
fetch without an AWS credential) and nothing else. Real MCP tool dispatch
(`server.call_tool`), same reasoning as `test_recall_residency.py`: proving
the plumbing, not just the underlying function.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import pytest
from mnemos_api.keys import Scope
from mnemos_api.server import build_server
from mnemos_engine.accountability import record_action
from mnemos_engine.ledger import checkpoint as take_checkpoint
from mnemos_engine.models import SourceTrust
from mnemos_warden.attestation import anchor_checkpoint

BUCKET = "mnemos-ledger-anchor-test"


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


async def _anchored_action(runtime, tenant: uuid.UUID) -> uuid.UUID:
    """An action with a real recall behind it, covered by a real checkpoint,
    anchored to a mocked S3 — the minimum an anchored deposition needs to
    exist at all."""
    subject = "applicant:explain-anchor"
    session = uuid.uuid4()
    episode = await runtime.engine.remember(
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
        cp = await take_checkpoint(cur, tenant)
        return action_id, cp.checkpoint_seq

    action_id, checkpoint_seq = await runtime.db.transaction(
        tenant, declare_and_checkpoint, label="declare_and_checkpoint"
    )

    mock_s3 = MagicMock()

    async def anchor(cur):
        await anchor_checkpoint(cur, tenant, checkpoint_seq, s3=mock_s3, bucket=BUCKET)

    await runtime.db.transaction(tenant, anchor, label="anchor")

    del episode  # only its side effects (the episode row) matter here
    return action_id


async def test_explain_presigns_the_anchor_when_s3_is_configured(
    server, runtime, tenant, as_principal
) -> None:
    as_principal(tenant, Scope.READ)
    action_id = await _anchored_action(runtime, tenant)

    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://example.com/signed-anchor"
    runtime.s3 = mock_s3
    runtime.settings = replace(runtime.settings, anchor_bucket=BUCKET)

    result = _payload(await server.call_tool("explain", {"action_id": str(action_id)}))

    assert result["anchored"] is True
    assert result["anchor_uri"] is not None
    assert result["anchor_presigned_url"] == "https://example.com/signed-anchor"
    assert result["anchor_presigned_url_expires_in"] == 3600
    mock_s3.generate_presigned_url.assert_called_once()
    call = mock_s3.generate_presigned_url.call_args
    assert call.args[0] == "get_object"
    assert call.kwargs["Params"]["Bucket"] == BUCKET


async def test_explain_leaves_presigned_url_null_when_s3_is_not_configured(
    server, runtime, tenant, as_principal
) -> None:
    """The default test runtime has no anchor bucket configured — the field
    must degrade to null, not raise, so `explain()` stays usable in every
    deployment that has not wired S3 Object Lock yet."""
    as_principal(tenant, Scope.READ)
    action_id = await _anchored_action(runtime, tenant)

    assert runtime.s3 is None
    assert runtime.settings.anchor_bucket is None

    result = _payload(await server.call_tool("explain", {"action_id": str(action_id)}))

    assert result["anchored"] is True
    assert result["anchor_presigned_url"] is None
    assert result["anchor_presigned_url_expires_in"] is None


async def test_explain_presign_failure_does_not_fail_the_deposition(
    server, runtime, tenant, as_principal
) -> None:
    """A presigning error (throttling, a misconfigured region) must not turn
    an otherwise-valid deposition into a hard failure — everything a caller
    needs to verify the chain themselves is already in the raw fields."""
    as_principal(tenant, Scope.READ)
    action_id = await _anchored_action(runtime, tenant)

    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.side_effect = RuntimeError("boom")
    runtime.s3 = mock_s3
    runtime.settings = replace(runtime.settings, anchor_bucket=BUCKET)

    result = _payload(await server.call_tool("explain", {"action_id": str(action_id)}))

    assert result["anchored"] is True
    assert result["anchor_uri"] is not None
    assert result["anchor_presigned_url"] is None
