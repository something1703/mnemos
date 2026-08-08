#!/usr/bin/env python3
"""Independently verify one Mnemos ledger checkpoint anchor.

This is the tool a skeptical judge runs, not us. It imports nothing from
Mnemos — not mnemos_engine, not psycopg — because a verifier that trusts our
code to check our own math proves nothing. It reimplements the two-line hash
construction from docs/ledger.md directly, from the standard library, and
recomputes the anchor's Merkle root from its own published shard heads.

What this proves: the anchor blob is internally consistent — its merkle_root
really is SHA-256-derived from its shard_heads, exactly as docs/ledger.md
specifies, with no fabrication possible between "here are our shard heads"
and "here is the root we claim they produce". What this does NOT prove: that
the live database still matches the anchor right now (that comparison is
`mnemos-attest verify`, which needs a database connection this script
deliberately does not have).

Two ways to fetch the anchor, so a judge needs nothing but Python:

    # A presigned URL (from `mnemos-attest presign`) — no AWS credentials,
    # no boto3, just the standard library:
    python3 independent_verify.py --url "https://mnemos-ledger-anchor-....s3.amazonaws.com/...?X-Amz-..."

    # Or, with your own AWS credentials and boto3 installed:
    python3 independent_verify.py --bucket mnemos-ledger-anchor-582054875648 \
        --tenant 11111111-1111-4111-8111-111111111111 --checkpoint 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from typing import Any

GENESIS = b"\x00" * 32


def merkle_root(leaves: list[bytes]) -> bytes:
    """Same construction as mnemos_engine.canonical.merkle_root, reimplemented
    from scratch here rather than imported — the entire point of this script
    is that it does not trust that module to be correct."""
    if not leaves:
        return GENESIS
    level = sorted(leaves)
    while len(level) > 1:
        nxt = [
            hashlib.sha256(level[i] + level[i + 1]).digest() for i in range(0, len(level) - 1, 2)
        ]
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return level[0]


def fetch_via_url(url: str) -> dict[str, Any]:
    # The URL is judge-supplied by design — this script's whole purpose is to
    # fetch and check a URL the caller was given.
    with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310
        result: dict[str, Any] = json.loads(response.read())
        return result


def fetch_via_boto3(bucket: str, tenant: str, checkpoint: int) -> dict[str, Any]:
    import boto3  # only imported on this path; not required for --url

    s3 = boto3.client("s3")
    key = f"checkpoints/{tenant}/{checkpoint:012d}.json"
    response = s3.get_object(Bucket=bucket, Key=key)
    result: dict[str, Any] = json.loads(response["Body"].read())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--url", help="presigned HTTPS URL to the anchor object")
    parser.add_argument("--bucket", help="S3 bucket name (requires boto3 + AWS credentials)")
    parser.add_argument("--tenant", help="tenant UUID (with --bucket)")
    parser.add_argument("--checkpoint", type=int, help="checkpoint sequence number (with --bucket)")
    args = parser.parse_args()

    if args.url:
        anchor = fetch_via_url(args.url)
    elif args.bucket and args.tenant and args.checkpoint is not None:
        anchor = fetch_via_boto3(args.bucket, args.tenant, args.checkpoint)
    else:
        parser.error("provide --url, or --bucket + --tenant + --checkpoint")  # exits

    claimed_root = anchor["merkle_root"]
    shard_heads = anchor["shard_heads"]

    leaves = [bytes.fromhex(head["hash"]) for head in shard_heads.values()]
    recomputed_root = merkle_root(leaves).hex()

    print(f"tenant:            {anchor['tenant_id']}")
    print(f"checkpoint_seq:    {anchor['checkpoint_seq']}")
    print(f"shards covered:    {len(shard_heads)}")
    print(f"entries covered:   {anchor['entry_count']}")
    print(f"anchored_at:       {anchor['anchored_at']}")
    print()
    print(f"claimed root:      {claimed_root}")
    print(f"recomputed root:   {recomputed_root}")
    print()

    if recomputed_root == claimed_root:
        print("MATCH — the claimed root is exactly what SHA-256 produces from the")
        print("published shard heads. No trust in Mnemos' code was required to check this.")
        return 0

    print("MISMATCH — the anchor is internally inconsistent. Either the object was")
    print("corrupted or the root was fabricated. Do not trust this checkpoint.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
