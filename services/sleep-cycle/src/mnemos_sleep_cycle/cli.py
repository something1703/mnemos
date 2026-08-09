"""`mnemos-sleep-cycle` — consolidate episodes into facts, decay what has gone
unused, quarantine what never earned corroboration.

    mnemos-sleep-cycle posture
    mnemos-sleep-cycle run-consolidation [--limit N]
    mnemos-sleep-cycle run-decay
    mnemos-sleep-cycle run-cycle           # both, in sequence

`run-consolidation` is the hourly light run and the nightly full run — the
only difference between them is `--limit`, matching Phase 05.6's design
(hourly capped at a handful of sessions, nightly given the full backlog).
`run-decay` is the weekly sweep. `run-cycle` exists for a single Lambda
invocation to drive the whole nightly path without needing Step Functions to
orchestrate two separate function calls, and is what the state machine's
Task states actually invoke (see infra/sleep-cycle/state_machine.json).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from uuid import UUID

import psycopg
from mnemos_engine.llm import LLMError

from .consolidate import (
    ConsolidationOutcome,
    SessionBatch,
    consolidate_batch,
    find_unconsolidated_batches,
)
from .corroboration import quarantine_stale_unverified
from .decay import decay_tenant_facts
from .runtime import Runtime, build_runtime

log = logging.getLogger("mnemos.sleep_cycle.cli")

EXIT_OK = 0
EXIT_ERROR = 2

ACTOR = "system:sleep-cycle"


async def _cmd_posture() -> int:
    runtime = await build_runtime()
    try:
        posture = runtime.settings.describe_posture()
    finally:
        await runtime.close()

    width = 62
    print("─" * width)
    print("  mnemos-sleep-cycle — configured posture")
    print("─" * width)
    for key, value in posture.items():
        print(f"  {key:<28} {value}")
    print("─" * width)
    if posture["database_role"] != "pipeline":
        print("  WARNING: running against the admin database role.")
        print("  Set MNEMOS_DB_URL_PIPELINE to a login granted mnemos_pipeline.")
        print("─" * width)
    return EXIT_OK


async def run_consolidation(runtime: Runtime, *, limit: int | None = None) -> dict[str, int]:
    """The reusable core of `run-consolidation`, also called by `run-cycle` and
    by tests that want the real gather-and-process path without going through
    argparse."""
    batch_limit = limit or runtime.settings.batch_limit

    async def gather(cur: psycopg.AsyncCursor) -> list[SessionBatch]:
        return await find_unconsolidated_batches(cur, limit=batch_limit)

    batches = await runtime.db.transaction(None, gather, label="find_batches", read_only=True)
    print(f"{len(batches)} batch(es) to consolidate")

    totals = {
        "novel": 0,
        "reinforced": 0,
        "superseded": 0,
        "contested": 0,
        "dropped": 0,
        "errors": 0,
    }
    for batch in batches:

        async def run(
            cur: psycopg.AsyncCursor, batch: SessionBatch = batch
        ) -> ConsolidationOutcome:
            return await consolidate_batch(
                cur,
                batch,
                chat=runtime.chat,
                embedder=runtime.embedder,
                envelope=runtime.envelope,
                actor=ACTOR,
            )

        try:
            outcome = await runtime.db.transaction(batch.tenant_id, run, label="consolidate_batch")
        except LLMError as exc:
            totals["errors"] += 1
            log.warning("consolidation failed for session %s: %s", batch.session_id, exc)
            print(
                f"  ERROR session={batch.session_id} subject={batch.subject_key}: {exc}",
                file=sys.stderr,
            )
            continue

        dropped = outcome.facts_dropped_no_source + outcome.facts_dropped_invalid
        totals["novel"] += outcome.facts_novel
        totals["reinforced"] += outcome.facts_reinforced
        totals["superseded"] += outcome.facts_superseded
        totals["contested"] += outcome.facts_contested
        totals["dropped"] += dropped
        print(
            f"  session={batch.session_id} subject={batch.subject_key}: "
            f"novel={outcome.facts_novel} reinforced={outcome.facts_reinforced} "
            f"superseded={outcome.facts_superseded} contested={outcome.facts_contested} "
            f"dropped={dropped}"
        )

    print(
        "totals: novel={novel} reinforced={reinforced} superseded={superseded} "
        "contested={contested} dropped={dropped} errors={errors}".format(**totals)
    )
    return totals


async def run_decay(runtime: Runtime) -> dict[str, int]:
    """The reusable core of `run-decay`."""

    async def list_tenants(cur: psycopg.AsyncCursor) -> list[tuple[UUID, str]]:
        await cur.execute("SELECT tenant_id, slug FROM mnemos.tenants")
        return [(row[0], str(row[1])) for row in await cur.fetchall()]

    tenants = await runtime.db.transaction(None, list_tenants, label="list_tenants", read_only=True)

    totals = {"decayed": 0, "quarantined": 0}
    for tenant_id, slug in tenants:

        async def run(cur: psycopg.AsyncCursor, tenant_id: UUID = tenant_id) -> tuple[int, int]:
            decayed = await decay_tenant_facts(
                cur, tenant_id, actor=ACTOR, lam=runtime.settings.decay_lambda
            )
            quarantined = await quarantine_stale_unverified(
                cur, tenant_id, actor=ACTOR, ttl_days=runtime.settings.corroboration_ttl_days
            )
            return decayed, quarantined

        decayed, quarantined = await runtime.db.transaction(tenant_id, run, label="decay_tenant")
        totals["decayed"] += decayed
        totals["quarantined"] += quarantined
        print(f"  {slug}: decayed={decayed} quarantined={quarantined}")

    print("totals: decayed={decayed} quarantined={quarantined}".format(**totals))
    return totals


async def _cmd_consolidate(limit: int | None) -> int:
    runtime = await build_runtime()
    try:
        await run_consolidation(runtime, limit=limit)
    finally:
        await runtime.close()
    return EXIT_OK


async def _cmd_decay() -> int:
    runtime = await build_runtime()
    try:
        await run_decay(runtime)
    finally:
        await runtime.close()
    return EXIT_OK


async def _cmd_cycle() -> int:
    runtime = await build_runtime()
    try:
        print("== consolidation ==")
        await run_consolidation(runtime)
        print("\n== decay ==")
        await run_decay(runtime)
    finally:
        await runtime.close()
    return EXIT_OK


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="mnemos-sleep-cycle", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("posture", help="show the configured security posture")

    consolidate_p = sub.add_parser(
        "run-consolidation", help="distill unconsolidated episodes into facts"
    )
    consolidate_p.add_argument(
        "--limit", type=int, default=None, help="overrides MNEMOS_SLEEP_BATCH_LIMIT"
    )

    sub.add_parser("run-decay", help="strength decay + stale-unverified quarantine, all tenants")
    sub.add_parser("run-cycle", help="consolidation then decay, in one process")

    args = parser.parse_args()

    try:
        if args.command == "posture":
            return asyncio.run(_cmd_posture())
        if args.command == "run-consolidation":
            return asyncio.run(_cmd_consolidate(args.limit))
        if args.command == "run-decay":
            return asyncio.run(_cmd_decay())
        return asyncio.run(_cmd_cycle())
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
