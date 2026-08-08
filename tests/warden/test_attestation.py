"""Attestation against the real S3 Object Lock bucket (ADR-013).

Marked `aws` rather than `invariant`: these need live AWS credentials and a
real bucket, so they are excluded from the default `make test` run and only
execute when explicitly requested (`make test-aws` / CI's manual AWS job).

The test that matters is `test_attacker_who_fools_the_database_does_not_fool_the_anchor`.
Everything else here is plumbing; that one is the actual claim.
"""

from __future__ import annotations

import os
import uuid

import boto3
import pytest
from mnemos_engine.canonical import GENESIS_HASH, entry_hash, payload_hash
from mnemos_engine.ledger import append_audit, checkpoint, verify_chain
from mnemos_engine.models import Op
from mnemos_warden.attestation import (
    AttestationMismatch,
    anchor_checkpoint,
    fetch_anchor,
    latest_checkpoint_seq,
    presign_anchor_url,
    verify_against_anchor,
)

pytestmark = pytest.mark.aws

BUCKET = os.environ.get("MNEMOS_S3_ANCHOR_BUCKET", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")


@pytest.fixture
def s3():
    if not BUCKET:
        pytest.skip("MNEMOS_S3_ANCHOR_BUCKET not set")
    return boto3.client("s3", region_name=REGION)


async def _append(db, tenant_id, subject: str) -> None:
    async def run(cur):
        await append_audit(cur, tenant_id, op=Op.REMEMBER, actor="test", subject_key=subject)

    await db.transaction(tenant_id, run, label="append")


async def test_anchor_and_verify_round_trip(db, tenant: uuid.UUID, s3) -> None:
    for i in range(4):
        await _append(db, tenant, f"subject:{i}")

    async def make_checkpoint(cur):
        return await checkpoint(cur, tenant)

    made = await db.transaction(tenant, make_checkpoint, label="checkpoint")

    async def anchor(cur):
        return await anchor_checkpoint(cur, tenant, made.checkpoint_seq, s3=s3, bucket=BUCKET)

    uri = await db.transaction(tenant, anchor, label="anchor")
    assert uri.startswith(f"s3://{BUCKET}/checkpoints/{tenant}/")

    fetched = fetch_anchor(
        s3=s3, bucket=BUCKET, tenant_id=tenant, checkpoint_seq=made.checkpoint_seq
    )
    assert fetched["merkle_root"] == made.merkle_root.hex()

    async def verify(cur):
        await verify_against_anchor(cur, tenant, made.checkpoint_seq, s3=s3, bucket=BUCKET)

    # Must not raise.
    await db.transaction(tenant, verify, label="verify", read_only=True)


async def test_anchor_updates_the_row(db, tenant: uuid.UUID, s3) -> None:
    await _append(db, tenant, "subject:anchor-row")

    async def make_checkpoint(cur):
        return await checkpoint(cur, tenant)

    made = await db.transaction(tenant, make_checkpoint, label="checkpoint")

    async def before(cur):
        await cur.execute(
            "SELECT anchor_uri, anchored_at FROM mnemos.chain_checkpoints "
            "WHERE tenant_id = %s AND checkpoint_seq = %s",
            (tenant, made.checkpoint_seq),
        )
        return await cur.fetchone()

    assert (await db.transaction(tenant, before, label="before")) == (None, None)

    async def anchor(cur):
        return await anchor_checkpoint(cur, tenant, made.checkpoint_seq, s3=s3, bucket=BUCKET)

    await db.transaction(tenant, anchor, label="anchor")
    uri, anchored_at = await db.transaction(tenant, before, label="after")
    assert uri is not None
    assert anchored_at is not None


async def test_verifying_a_tampered_entry_without_checkpoint_rewrite_still_fails(
    db, tenant: uuid.UUID, s3
) -> None:
    """The cheap attack: edit one entry, don't bother touching
    chain_checkpoints. Caught by the live-lookup inside verify_against_anchor
    itself — the recomputed root won't match even before reaching S3's
    version of events."""
    await _append(db, tenant, "subject:cheap-tamper")

    async def make_checkpoint(cur):
        return await checkpoint(cur, tenant)

    made = await db.transaction(tenant, make_checkpoint, label="checkpoint")

    async def anchor(cur):
        return await anchor_checkpoint(cur, tenant, made.checkpoint_seq, s3=s3, bucket=BUCKET)

    await db.transaction(tenant, anchor, label="anchor")

    async def tamper(cur):
        await cur.execute(
            "UPDATE mnemos.audit_chain SET entry_hash = %s "
            "WHERE tenant_id = %s AND subject_key = 'subject:cheap-tamper'",
            (b"\xff" * 32, tenant),
        )

    await db.transaction(tenant, tamper, label="tamper")

    async def verify(cur):
        await verify_against_anchor(cur, tenant, made.checkpoint_seq, s3=s3, bucket=BUCKET)

    with pytest.raises(AttestationMismatch):
        await db.transaction(tenant, verify, label="verify", read_only=True)


async def test_attacker_who_fools_the_database_does_not_fool_the_anchor(
    db, tenant: uuid.UUID, s3
) -> None:
    """THE claim. An attacker with database-admin rights rewrites a shard's
    entries AND recomputes chain_checkpoints.merkle_root to match — an
    internally consistent forgery. `mnemos-verify` (the plain in-database
    check) is fooled: both tables agree with each other, because the attacker
    controls both. `mnemos-attest` is not, because the root committed to S3
    before the rewrite cannot be touched by anyone with only database access.
    """
    for _ in range(3):
        await _append(db, tenant, "subject:attack-target")

    async def make_checkpoint(cur):
        return await checkpoint(cur, tenant)

    made = await db.transaction(tenant, make_checkpoint, label="checkpoint")

    async def anchor(cur):
        return await anchor_checkpoint(cur, tenant, made.checkpoint_seq, s3=s3, bucket=BUCKET)

    await db.transaction(tenant, anchor, label="anchor")

    # The forgery: rewrite the target shard's entries AND the checkpoint row
    # that describes it, consistently, exactly as a database administrator
    # with full DML rights could.
    async def forge(cur):
        await cur.execute(
            "SELECT shard_id, seq FROM mnemos.audit_chain "
            "WHERE tenant_id = %s AND subject_key = 'subject:attack-target' ORDER BY seq",
            (tenant,),
        )
        rows = await cur.fetchall()
        prev = GENESIS_HASH
        new_heads: dict[int, tuple[int, bytes]] = {}
        for shard_id, seq in rows:
            body = {"op": "remember", "actor": "attacker", "seq": int(seq), "forged": True}
            digest = payload_hash(body)
            entry = entry_hash(digest, prev)
            await cur.execute(
                "UPDATE mnemos.audit_chain SET payload = %s, payload_hash = %s, "
                "prev_hash = %s, entry_hash = %s "
                "WHERE tenant_id = %s AND shard_id = %s AND seq = %s",
                (_json(body), digest, prev, entry, tenant, shard_id, seq),
            )
            new_heads[int(shard_id)] = (int(seq), entry)
            prev = entry

        for shard_id, (_seq, entry) in new_heads.items():
            await cur.execute(
                "UPDATE mnemos.chain_heads SET entry_hash = %s "
                "WHERE tenant_id = %s AND shard_id = %s",
                (entry, tenant, shard_id),
            )

        # Recompute and overwrite the checkpoint's own stored root too —
        # the attacker controls chain_checkpoints as well.
        await cur.execute(
            "SELECT shard_id, seq, entry_hash FROM mnemos.chain_heads "
            "WHERE tenant_id = %s ORDER BY shard_id",
            (tenant,),
        )
        heads = await cur.fetchall()
        from mnemos_engine.canonical import merkle_root

        forged_root = merkle_root([bytes(r[2]) for r in heads])
        forged_shard_heads = {
            str(int(r[0])): {"seq": int(r[1]), "hash": bytes(r[2]).hex()} for r in heads
        }
        await cur.execute(
            "UPDATE mnemos.chain_checkpoints SET merkle_root = %s, shard_heads = %s "
            "WHERE tenant_id = %s AND checkpoint_seq = %s",
            (forged_root, _json(forged_shard_heads), tenant, made.checkpoint_seq),
        )

    await db.transaction(tenant, forge, label="forge")

    # 1. The in-database verifier is fooled: everything it can see agrees.
    async def plain_verify(cur):
        return await verify_chain(cur, tenant)

    plain_result = await db.transaction(tenant, plain_verify, label="plain_verify")
    assert plain_result.valid, (
        "expected the DATABASE-ONLY check to be fooled by an internally "
        "consistent forgery — if it is not, this test no longer demonstrates "
        "why the S3 anchor is necessary"
    )

    # 2. The anchor-backed verifier is NOT fooled.
    async def anchored_verify(cur):
        await verify_against_anchor(cur, tenant, made.checkpoint_seq, s3=s3, bucket=BUCKET)

    with pytest.raises(AttestationMismatch) as exc:
        await db.transaction(tenant, anchored_verify, label="anchored_verify", read_only=True)
    assert exc.value.anchored_root != exc.value.live_root


async def test_latest_checkpoint_seq_tracks_new_checkpoints(db, tenant: uuid.UUID) -> None:
    await _append(db, tenant, "subject:seq")

    async def none_yet(cur):
        return await latest_checkpoint_seq(cur, tenant)

    assert await db.transaction(tenant, none_yet, label="check") is None

    async def make_checkpoint(cur):
        return await checkpoint(cur, tenant)

    first = await db.transaction(tenant, make_checkpoint, label="checkpoint")
    assert await db.transaction(tenant, none_yet, label="check") == first.checkpoint_seq


async def test_presigned_url_fetches_the_same_content_as_direct_access(
    db, tenant: uuid.UUID, s3
) -> None:
    """The link a judge actually gets. Fetched here with plain urllib — no
    boto3, no AWS credentials — mirroring exactly what
    scripts/independent_verify.py does, so the round trip is proven by an
    automated test rather than only by the manual run recorded in ADR-013."""
    import json
    import urllib.request

    await _append(db, tenant, "subject:presign")

    async def make_checkpoint(cur):
        return await checkpoint(cur, tenant)

    made = await db.transaction(tenant, make_checkpoint, label="checkpoint")

    async def anchor(cur):
        return await anchor_checkpoint(cur, tenant, made.checkpoint_seq, s3=s3, bucket=BUCKET)

    await db.transaction(tenant, anchor, label="anchor")

    url = presign_anchor_url(
        s3=s3, bucket=BUCKET, tenant_id=tenant, checkpoint_seq=made.checkpoint_seq, expires_in=120
    )
    assert url.startswith("https://")
    assert "Signature=" in url or "X-Amz-Signature=" in url

    # S310: our own presigned URL, not user input. ASYNC210: a single quick
    # fetch mirroring exactly what the (deliberately synchronous) standalone
    # script does — not a hot path worth threading off.
    with urllib.request.urlopen(url, timeout=15) as response:  # noqa: ASYNC210
        fetched = json.loads(response.read())

    assert fetched["merkle_root"] == made.merkle_root.hex()


def _json(value):
    from psycopg.types.json import Json

    return Json(value)
