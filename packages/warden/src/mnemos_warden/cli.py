"""`mnemos-attest` — anchor a checkpoint to S3 Object Lock, or verify one.

    mnemos-attest anchor --tenant clinic
    mnemos-attest verify --tenant clinic
    mnemos-attest verify --tenant clinic --checkpoint 3

Exit codes match `mnemos-verify`: 0 = valid/anchored, 1 = mismatch detected,
2 = operating error. A verifier nobody can script into CI is a verifier
nobody runs.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID

import boto3
import psycopg
from mnemos_engine.db import Database
from mypy_boto3_s3 import S3Client

from .attestation import (
    AttestationMismatch,
    anchor_checkpoint,
    latest_checkpoint_seq,
    presign_anchor_url,
    verify_against_anchor,
)
from .errors import UnknownSubject

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_ERROR = 2


async def _resolve_tenant(db: Database, tenant: str) -> UUID | None:
    try:
        return UUID(tenant)
    except ValueError:
        pass

    async def lookup(cur: psycopg.AsyncCursor) -> tuple[UUID] | None:
        await cur.execute("SELECT tenant_id FROM mnemos.tenants WHERE slug = %s", (tenant,))
        return await cur.fetchone()

    row = await db.transaction(None, lookup, label="resolve_tenant")
    return row[0] if row else None


async def _cmd_anchor(db: Database, s3: S3Client, bucket: str, tenant_arg: str) -> int:
    tenant_id = await _resolve_tenant(db, tenant_arg)
    if tenant_id is None:
        print(f"no tenant matching {tenant_arg!r}", file=sys.stderr)
        return EXIT_ERROR

    seq = await db.transaction(
        tenant_id, lambda cur: latest_checkpoint_seq(cur, tenant_id), label="latest_seq"
    )
    if seq is None:
        print(
            f"no checkpoint exists for tenant {tenant_arg!r} yet — run the ledger's "
            "checkpoint() first (mnemos_engine.ledger.checkpoint)",
            file=sys.stderr,
        )
        return EXIT_ERROR

    async def run(cur: psycopg.AsyncCursor) -> str:
        return await anchor_checkpoint(cur, tenant_id, seq, s3=s3, bucket=bucket)

    try:
        uri = await db.transaction(tenant_id, run, label="anchor")
    except UnknownSubject as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR

    print(f"anchored checkpoint {seq} for {tenant_arg} -> {uri}")
    return EXIT_OK


async def _cmd_verify(
    db: Database, s3: S3Client, bucket: str, tenant_arg: str, checkpoint: int | None
) -> int:
    tenant_id = await _resolve_tenant(db, tenant_arg)
    if tenant_id is None:
        print(f"no tenant matching {tenant_arg!r}", file=sys.stderr)
        return EXIT_ERROR

    seq = checkpoint
    if seq is None:
        seq = await db.transaction(
            tenant_id, lambda cur: latest_checkpoint_seq(cur, tenant_id), label="latest_seq"
        )
    if seq is None:
        print(f"no checkpoint exists for tenant {tenant_arg!r}", file=sys.stderr)
        return EXIT_ERROR

    async def run(cur: psycopg.AsyncCursor) -> None:
        await verify_against_anchor(cur, tenant_id, seq, s3=s3, bucket=bucket)

    width = 62
    print("─" * width)
    print(f"  mnemos-attest — tenant {tenant_arg}, checkpoint {seq}")
    print("─" * width)
    try:
        await db.transaction(tenant_id, run, label="verify_anchor", read_only=True)
    except AttestationMismatch as exc:
        print("  MISMATCH")
        print(f"  anchored root  {exc.anchored_root}")
        print(f"  live root      {exc.live_root}")
        print("─" * width)
        print("  History was rewritten inside CockroachDB after this checkpoint")
        print("  was anchored to S3 Object Lock. The anchor is the source of truth.")
        return EXIT_MISMATCH
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print("  MATCH — the live chain agrees with the anchor committed outside")
    print("  the database. A whole-shard rewrite consistent within CockroachDB")
    print("  itself would not have passed this check.")
    print("─" * width)
    return EXIT_OK


async def _cmd_presign(
    db: Database,
    s3: S3Client,
    bucket: str,
    tenant_arg: str,
    checkpoint: int | None,
    expires_in: int,
) -> int:
    """Print a time-limited link to one anchor — the thing to hand a judge so
    they can run scripts/independent_verify.py --url without ever holding an
    AWS credential. The bucket stays private (ADR-013); this is the
    deliberate alternative to making it public."""
    tenant_id = await _resolve_tenant(db, tenant_arg)
    if tenant_id is None:
        print(f"no tenant matching {tenant_arg!r}", file=sys.stderr)
        return EXIT_ERROR

    seq = checkpoint
    if seq is None:
        seq = await db.transaction(
            tenant_id, lambda cur: latest_checkpoint_seq(cur, tenant_id), label="latest_seq"
        )
    if seq is None:
        print(f"no checkpoint exists for tenant {tenant_arg!r}", file=sys.stderr)
        return EXIT_ERROR

    url = presign_anchor_url(
        s3=s3, bucket=bucket, tenant_id=tenant_id, checkpoint_seq=seq, expires_in=expires_in
    )
    print(url)
    print(
        f"# valid for {expires_in}s — verify independently with:\n"
        f'#   python3 scripts/independent_verify.py --url "{url}"',
        file=sys.stderr,
    )
    return EXIT_OK


def main() -> int:
    parser = argparse.ArgumentParser(prog="mnemos-attest", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    anchor_p = sub.add_parser("anchor", help="anchor the latest checkpoint to S3 Object Lock")
    anchor_p.add_argument("--tenant", required=True)

    verify_p = sub.add_parser("verify", help="verify the live chain against an anchored root")
    verify_p.add_argument("--tenant", required=True)
    verify_p.add_argument("--checkpoint", type=int, default=None, help="defaults to latest")

    presign_p = sub.add_parser(
        "presign", help="print a time-limited URL to one anchor, for sharing without AWS creds"
    )
    presign_p.add_argument("--tenant", required=True)
    presign_p.add_argument("--checkpoint", type=int, default=None, help="defaults to latest")
    presign_p.add_argument("--expires-in", type=int, default=3600, help="seconds, default 1h")

    parser.add_argument("--url", default=os.environ.get("MNEMOS_DB_URL"))
    parser.add_argument("--bucket", default=os.environ.get("MNEMOS_S3_ANCHOR_BUCKET"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    args = parser.parse_args()

    if not args.url or args.url.startswith("postgresql://<"):
        print("No database URL. Set MNEMOS_DB_URL or pass --url.", file=sys.stderr)
        return EXIT_ERROR
    if not args.bucket:
        print("No anchor bucket. Set MNEMOS_S3_ANCHOR_BUCKET or pass --bucket.", file=sys.stderr)
        return EXIT_ERROR

    async def run() -> int:
        db = Database(args.url, min_size=1, max_size=2)
        await db.open()
        s3: S3Client = boto3.client("s3", region_name=args.region)
        try:
            if args.command == "anchor":
                return await _cmd_anchor(db, s3, args.bucket, args.tenant)
            if args.command == "presign":
                return await _cmd_presign(
                    db, s3, args.bucket, args.tenant, args.checkpoint, args.expires_in
                )
            return await _cmd_verify(db, s3, args.bucket, args.tenant, args.checkpoint)
        finally:
            await db.close()

    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
