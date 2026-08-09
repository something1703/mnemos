"""attestation.py against a real local ledger and a mocked S3 client.

`tests/warden/test_attestation.py` is `@pytest.mark.aws` — it anchors to and
reads from the real S3 Object Lock bucket, which is the only way to prove
Object Lock's own immutability guarantee. Everything in THIS file is the
surrounding logic that does not need a real bucket to be correct: the
checkpoint payload construction, the round-trip through anchor/verify, the
mismatch and missing-entry detection, and the presigned-URL parameters — all
exercised against a real CockroachDB ledger with `boto3`'s S3 client
replaced by a `MagicMock`. Before this file, none of it ran outside the
AWS-marked suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from mnemos_engine.ledger import checkpoint as take_checkpoint
from mnemos_engine.models import SourceTrust
from mnemos_warden.attestation import (
    AttestationMismatch,
    anchor_checkpoint,
    latest_checkpoint_seq,
    presign_anchor_url,
    retention_expiry_estimate,
    verify_against_anchor,
)
from mnemos_warden.errors import UnknownSubject

BUCKET = "mnemos-ledger-anchor-test"


@pytest.fixture
def s3() -> MagicMock:
    return MagicMock()


async def _write_one_episode(engine_for, tenant_id: uuid.UUID) -> None:
    """The cheapest way to get a real, non-empty chain: one remember() call,
    which appends a real audit_chain entry via the same code path production
    uses."""
    engine = engine_for(tenant_id)
    await engine.remember(
        tenant_id,
        subject_key="patient:attest-test",
        session_id=uuid.uuid4(),
        event_type="note",
        content="one real episode, so the chain has something to checkpoint",
        source_trust=SourceTrust.OPERATOR,
    )


async def test_anchor_checkpoint_puts_the_payload_and_updates_the_row(
    db, tenant, engine_for, s3
) -> None:
    await _write_one_episode(engine_for, tenant)

    async def take(cur):
        return await take_checkpoint(cur, tenant, actor="test")

    cp = await db.transaction(tenant, take, label="checkpoint")

    async def anchor(cur):
        return await anchor_checkpoint(cur, tenant, cp.checkpoint_seq, s3=s3, bucket=BUCKET)

    uri = await db.transaction(tenant, anchor, label="anchor")

    assert uri == f"s3://{BUCKET}/checkpoints/{tenant}/{cp.checkpoint_seq:012d}.json"
    s3.put_object.assert_called_once()
    call = s3.put_object.call_args.kwargs
    assert call["Bucket"] == BUCKET
    assert call["Key"] == f"checkpoints/{tenant}/{cp.checkpoint_seq:012d}.json"
    assert call["ContentType"] == "application/json"

    async def read_row(cur):
        await cur.execute(
            "SELECT anchor_uri, anchored_at FROM mnemos.chain_checkpoints "
            "WHERE tenant_id = %s AND checkpoint_seq = %s",
            (tenant, cp.checkpoint_seq),
        )
        return await cur.fetchone()

    row = await db.transaction(tenant, read_row, label="read", read_only=True)
    assert row[0] == uri
    assert row[1] is not None


async def test_anchor_checkpoint_unknown_checkpoint_raises(db, tenant, s3) -> None:
    async def anchor(cur):
        return await anchor_checkpoint(cur, tenant, 999, s3=s3, bucket=BUCKET)

    with pytest.raises(UnknownSubject):
        await db.transaction(tenant, anchor, label="anchor")
    s3.put_object.assert_not_called()


async def test_verify_against_anchor_passes_when_live_chain_matches(
    db, tenant, engine_for, s3
) -> None:
    """The full round trip: anchor a real checkpoint, capture exactly what
    was PUT, hand that same payload back on GET, and confirm verification
    recomputes the same root and raises nothing."""
    await _write_one_episode(engine_for, tenant)

    async def take(cur):
        return await take_checkpoint(cur, tenant, actor="test")

    cp = await db.transaction(tenant, take, label="checkpoint")

    captured: dict[str, bytes] = {}

    def fake_put_object(*, Bucket, Key, Body, ContentType):  # noqa: N803 - matches boto3's own kwargs
        captured["body"] = Body

    s3.put_object.side_effect = fake_put_object
    s3.get_object.return_value = {
        "Body": type("Stream", (), {"read": lambda self: captured["body"]})()
    }

    async def anchor(cur):
        return await anchor_checkpoint(cur, tenant, cp.checkpoint_seq, s3=s3, bucket=BUCKET)

    await db.transaction(tenant, anchor, label="anchor")

    async def verify(cur):
        await verify_against_anchor(cur, tenant, cp.checkpoint_seq, s3=s3, bucket=BUCKET)

    await db.transaction(tenant, verify, label="verify", read_only=True)  # raises on failure


async def test_verify_against_anchor_raises_on_missing_live_entry(db, tenant, s3) -> None:
    """The anchor references a shard/seq that no longer exists in the live
    chain — exactly the shape a `forget`-style rewrite (not through the
    Warden's real, audited path) would produce."""
    s3.get_object.return_value = {
        "Body": type(
            "Stream",
            (),
            {
                "read": lambda self: (
                    b'{"merkle_root": "aa", "shard_heads": {"0": {"seq": 999, "hash": "bb"}}}'
                )
            },
        )()
    }

    async def verify(cur):
        await verify_against_anchor(cur, tenant, 1, s3=s3, bucket=BUCKET)

    with pytest.raises(AttestationMismatch, match="MISSING"):
        await db.transaction(tenant, verify, label="verify", read_only=True)


async def test_verify_against_anchor_raises_on_root_mismatch(db, tenant, engine_for, s3) -> None:
    """A live chain that is internally self-consistent but whose recomputed
    root disagrees with what was anchored — the case a whole-shard-rewrite
    forgery produces, and the entire reason anchoring exists (docs/ledger.md
    §5): comparing a checkpoint against its own stored shard heads would not
    catch this; comparing against the anchor does."""
    await _write_one_episode(engine_for, tenant)

    async def take(cur):
        return await take_checkpoint(cur, tenant, actor="test")

    cp = await db.transaction(tenant, take, label="checkpoint")

    async def read_heads(cur):
        await cur.execute(
            "SELECT shard_heads FROM mnemos.chain_checkpoints "
            "WHERE tenant_id = %s AND checkpoint_seq = %s",
            (tenant, cp.checkpoint_seq),
        )
        return (await cur.fetchone())[0]

    real_heads = await db.transaction(tenant, read_heads, label="read", read_only=True)

    import json

    forged = json.dumps({"merkle_root": "0" * 64, "shard_heads": dict(real_heads)}).encode()
    s3.get_object.return_value = {"Body": type("Stream", (), {"read": lambda self: forged})()}

    async def verify(cur):
        await verify_against_anchor(cur, tenant, cp.checkpoint_seq, s3=s3, bucket=BUCKET)

    with pytest.raises(AttestationMismatch) as exc_info:
        await db.transaction(tenant, verify, label="verify", read_only=True)
    assert exc_info.value.anchored_root == "0" * 64


async def test_latest_checkpoint_seq_none_when_no_checkpoints(db, tenant) -> None:
    async def run(cur):
        return await latest_checkpoint_seq(cur, tenant)

    assert await db.transaction(tenant, run, label="run", read_only=True) is None


async def test_latest_checkpoint_seq_returns_the_max(db, tenant, engine_for) -> None:
    await _write_one_episode(engine_for, tenant)

    async def take(cur):
        return await take_checkpoint(cur, tenant, actor="test")

    first = await db.transaction(tenant, take, label="checkpoint1")

    async def run(cur):
        return await latest_checkpoint_seq(cur, tenant)

    assert await db.transaction(tenant, run, label="run", read_only=True) == first.checkpoint_seq


def test_presign_anchor_url_uses_the_right_key_and_params(s3: MagicMock) -> None:
    # A plain UUID rather than the `tenant` fixture on purpose: this function
    # touches no database, and `tenant` is an async fixture that a sync test
    # cannot resolve correctly.
    tenant_id = uuid.uuid4()
    s3.generate_presigned_url.return_value = "https://example.com/signed"
    url = presign_anchor_url(
        s3=s3, bucket=BUCKET, tenant_id=tenant_id, checkpoint_seq=3, expires_in=900
    )

    assert url == "https://example.com/signed"
    s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": BUCKET, "Key": f"checkpoints/{tenant_id}/{3:012d}.json"},
        ExpiresIn=900,
    )


def test_retention_expiry_estimate_adds_the_retention_window() -> None:
    anchored_at = datetime(2026, 1, 1, tzinfo=UTC)
    expiry = retention_expiry_estimate(anchored_at, retention_days=7)
    assert expiry == datetime(2026, 1, 8, tzinfo=UTC)
