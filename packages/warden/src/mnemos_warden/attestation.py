"""Anchoring the ledger outside the database — ADR-010, ADR-013.

`docs/ledger.md` §5 names three verification checks and is explicit that
skipping the third makes the second vacuous: comparing a checkpoint's Merkle
root against ITS OWN recorded shard heads proves nothing about an adversary
who can edit both `audit_chain` and `chain_checkpoints` consistently — a
database administrator, or anyone with the right grants.

This module is what makes that adversary detectable. `anchor_checkpoint`
writes the checkpoint's Merkle root to an S3 bucket with Object Lock in
compliance mode (real bucket, real 7-day retention, verified empirically —
see ADR-013): once written, that object cannot be altered or deleted by
anyone, including AWS account root, until the retention window expires.
`verify_against_anchor` then recomputes the live chain's state and compares
it against the ANCHORED root, not the database's own copy of it — so a
rewrite that is internally consistent inside CockroachDB still diverges from
what S3 was told at the time.

The anchor bucket blocks all public access by default (ADR-013 deviates here
from the phase sketch's "public-readable" ambition — a private bucket plus
presigned URLs for sharing a specific anchor is the safer default; public S3
buckets are a leading cause of real incidents and the Merkle root gains
nothing from being world-readable by default).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from mnemos_engine.canonical import merkle_root
from mypy_boto3_s3 import S3Client

from .errors import UnknownSubject

log = logging.getLogger("mnemos.warden.attestation")

ANCHOR_PREFIX = "checkpoints"


class AttestationMismatch(RuntimeError):
    """The live chain diverges from an anchored root.

    This is the exception a `docker kill`-style tamper drill is supposed to
    raise — it means someone rewrote history inside CockroachDB after the
    anchor was committed, and the rewrite is now provably detected by
    something outside the database's own control.
    """

    def __init__(self, tenant_id: UUID, checkpoint_seq: int, anchored: str, live: str) -> None:
        self.tenant_id = tenant_id
        self.checkpoint_seq = checkpoint_seq
        self.anchored_root = anchored
        self.live_root = live
        super().__init__(
            f"tenant {tenant_id} checkpoint {checkpoint_seq}: anchored root {anchored} "
            f"does not match the live chain's root {live} — history was rewritten after "
            "the anchor was committed"
        )


def _anchor_key(tenant_id: UUID, checkpoint_seq: int) -> str:
    return f"{ANCHOR_PREFIX}/{tenant_id}/{checkpoint_seq:012d}.json"


def _anchor_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


async def anchor_checkpoint(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    checkpoint_seq: int,
    *,
    s3: S3Client,
    bucket: str,
) -> str:
    """Write one checkpoint's Merkle root to Object Lock storage.

    Idempotent by construction: the S3 key is deterministic
    (`checkpoints/<tenant>/<seq>.json`), and re-anchoring an already-anchored
    checkpoint overwrites with byte-identical content — Object Lock permits
    overwriting a key with a NEW version (each PUT creates a new, separately
    locked version under S3 versioning), so this never fails, it just adds a
    version nobody needed.
    """
    await cur.execute(
        "SELECT merkle_root, shard_heads, entry_count, covers_through "
        "FROM mnemos.chain_checkpoints WHERE tenant_id = %s AND checkpoint_seq = %s",
        (tenant_id, checkpoint_seq),
    )
    row = await cur.fetchone()
    if row is None:
        raise UnknownSubject(f"no checkpoint {checkpoint_seq} for tenant {tenant_id}")

    stored_root, shard_heads, entry_count, covers_through = row
    payload: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "checkpoint_seq": checkpoint_seq,
        "merkle_root": bytes(stored_root).hex(),
        "shard_heads": dict(shard_heads),
        "entry_count": int(entry_count),
        "covers_through": covers_through.isoformat(),
        "anchored_at": datetime.now(UTC).isoformat(),
    }
    body = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")

    key = _anchor_key(tenant_id, checkpoint_seq)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
    )

    uri = _anchor_uri(bucket, key)
    await cur.execute(
        "UPDATE mnemos.chain_checkpoints SET anchor_uri = %s, anchored_at = now() "
        "WHERE tenant_id = %s AND checkpoint_seq = %s",
        (uri, tenant_id, checkpoint_seq),
    )
    log.info(
        "checkpoint anchored",
        extra={"tenant_id": str(tenant_id), "checkpoint_seq": checkpoint_seq, "uri": uri},
    )
    return uri


def fetch_anchor(
    *, s3: S3Client, bucket: str, tenant_id: UUID, checkpoint_seq: int
) -> dict[str, Any]:
    """Read an anchor back from S3. Synchronous — boto3 has no async client,
    and this is a single small GET, not a hot path."""
    key = _anchor_key(tenant_id, checkpoint_seq)
    response = s3.get_object(Bucket=bucket, Key=key)
    payload: dict[str, Any] = json.loads(response["Body"].read())
    return payload


async def verify_against_anchor(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    checkpoint_seq: int,
    *,
    s3: S3Client,
    bucket: str,
) -> None:
    """Recompute the live chain's root for this checkpoint's shard heads and
    compare against the ANCHORED copy, not the database's own record of it.

    Raises AttestationMismatch on divergence; returns normally (no return
    value — silence is the pass) when the two agree, mirroring the shape of
    an assertion rather than a query.
    """
    anchor = fetch_anchor(s3=s3, bucket=bucket, tenant_id=tenant_id, checkpoint_seq=checkpoint_seq)
    anchored_root = anchor["merkle_root"]

    live_leaves: list[bytes] = []
    for shard_key, head in anchor["shard_heads"].items():
        shard_id = int(shard_key)
        seq = int(head["seq"])
        await cur.execute(
            "SELECT entry_hash FROM mnemos.audit_chain "
            "WHERE tenant_id = %s AND shard_id = %s AND seq = %s",
            (tenant_id, shard_id, seq),
        )
        live_row = await cur.fetchone()
        if live_row is None:
            raise AttestationMismatch(
                tenant_id, checkpoint_seq, anchored_root, "MISSING — entry no longer exists"
            )
        live_leaves.append(bytes(live_row[0]))

    live_root = merkle_root(live_leaves).hex()
    if live_root != anchored_root:
        raise AttestationMismatch(tenant_id, checkpoint_seq, anchored_root, live_root)


async def latest_checkpoint_seq(cur: psycopg.AsyncCursor, tenant_id: UUID) -> int | None:
    await cur.execute(
        "SELECT MAX(checkpoint_seq) FROM mnemos.chain_checkpoints WHERE tenant_id = %s",
        (tenant_id,),
    )
    row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else None


def presign_anchor_url(
    *, s3: S3Client, bucket: str, tenant_id: UUID, checkpoint_seq: int, expires_in: int = 3600
) -> str:
    """A time-limited link to one anchor, for sharing with someone (a judge)
    who should be able to fetch and verify a specific root without holding
    standing AWS credentials. The bucket stays private; this is the
    deliberate alternative to making it public (see module docstring)."""
    key = _anchor_key(tenant_id, checkpoint_seq)
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )


def retention_expiry_estimate(anchored_at: datetime, retention_days: int) -> datetime:
    """For display only — the bucket's own Object Lock configuration is the
    authority on actual retention, not this arithmetic. Useful for the
    console to show 'locked until ~<date>' without an extra S3 call."""
    return anchored_at + timedelta(days=retention_days)
