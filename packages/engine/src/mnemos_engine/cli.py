"""`mnemos-verify` — walk a tenant's chain and recompute every hash.

Exit status is the point: 0 for VALID, 1 for a broken chain, 2 for an operating
error. A verifier that reports failure in prose but exits 0 cannot be used in a
cron job, and a proof nobody can automate is a proof nobody runs.

    mnemos-verify --tenant clinic
    mnemos-verify --tenant clinic --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any
from uuid import UUID

import psycopg

from .db import Database
from .ledger import verify_chain
from .models import VerificationResult

EXIT_VALID = 0
EXIT_BROKEN = 1
EXIT_ERROR = 2


async def _run(dsn: str, tenant: str, as_json: bool) -> int:
    db: Database = Database(dsn, min_size=1, max_size=2)
    try:
        await db.open()
    except Exception as exc:
        print(f"could not connect: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        tenant_id = await _resolve_tenant(db, tenant)
        if tenant_id is None:
            print(f"no tenant matching {tenant!r}", file=sys.stderr)
            return EXIT_ERROR

        async def run(cur: psycopg.AsyncCursor) -> VerificationResult:
            return await verify_chain(cur, tenant_id)

        result = await db.transaction(tenant_id, run, label="verify", read_only=True)
    finally:
        await db.close()

    if as_json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        _print_human(tenant, result)

    return EXIT_VALID if result.valid else EXIT_BROKEN


async def _resolve_tenant(db: Database, tenant: str) -> UUID | None:
    """Accept either a slug or a UUID, because operators use both."""
    try:
        return UUID(tenant)
    except ValueError:
        pass

    async def lookup(cur: psycopg.AsyncCursor) -> tuple[Any, ...] | None:
        await cur.execute("SELECT tenant_id FROM mnemos.tenants WHERE slug = %s", (tenant,))
        return await cur.fetchone()

    row = await db.transaction(None, lookup, label="resolve_tenant")
    return row[0] if row else None


def _print_human(tenant: str, result: VerificationResult) -> None:
    width = 62
    print("─" * width)
    print(f"  mnemos-verify — tenant {tenant}")
    print("─" * width)
    print(f"  entries checked     {result.entries_checked}")
    print(f"  shards checked      {result.shards_checked}")
    print(f"  checkpoints checked {result.checkpoints_checked}")
    print("─" * width)

    if result.valid:
        print("  VALID — every hash recomputes")
        if result.checkpoints_checked == 0:
            # Say so plainly. Per-entry hashes alone cannot catch a consistent
            # full-shard rewrite (docs/ledger.md §5.3), so a chain with no
            # checkpoints is weaker than this VALID badge might suggest.
            print()
            print("  NOTE: no checkpoints exist for this tenant, so a consistent")
            print("        whole-shard rewrite would not be detectable. Run a")
            print("        checkpoint and anchor it to strengthen this result.")
    else:
        print("  BROKEN")
        if result.broken_at:
            shard, seq = result.broken_at
            print(f"  first break at      shard {shard}, seq {seq}")
        print(f"  detail              {result.detail}")
    print("─" * width)


def main() -> int:
    parser = argparse.ArgumentParser(prog="mnemos-verify", description=__doc__)
    parser.add_argument("--tenant", required=True, help="tenant slug or UUID")
    parser.add_argument("--url", default=os.environ.get("MNEMOS_DB_URL"))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    if not args.url or args.url.startswith("postgresql://<"):
        print("No database URL. Set MNEMOS_DB_URL or pass --url.", file=sys.stderr)
        return EXIT_ERROR

    try:
        return asyncio.run(_run(args.url, args.tenant, args.as_json))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
