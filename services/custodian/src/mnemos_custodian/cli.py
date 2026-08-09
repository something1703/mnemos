"""`mnemos-custodian` — run one sweep and exit.

This is the ECS Fargate task's entrypoint (PHASE_07 7.6): EventBridge
starts a task on a schedule or in response to a CloudWatch alarm, the task
runs this once, and exits. No server, no long-lived process — the same
shape `mnemos-sleep-cycle`'s Lambda handler uses for the same reason (a
scheduled batch job has no request to hold open).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from typing import Any
from uuid import UUID

import psycopg

from .findings import TriggerSource
from .runtime import build_runtime
from .sweep import run_sweep

log = logging.getLogger("mnemos.custodian.cli")


async def _run(trigger_source: TriggerSource, trigger_detail: str | None) -> int:
    async with build_runtime() as runtime:
        session_id = uuid.uuid4()

        async def run(cur: psycopg.AsyncCursor) -> UUID:
            return await run_sweep(
                cur,
                runtime.tenant_id,
                trigger_source=trigger_source,
                trigger_detail=trigger_detail,
                skills=runtime.skills,
                mcp=runtime.mcp,
                chat=runtime.chat,
                fact_writer=runtime.fact_writer,
                session_id=session_id,
                database=runtime.database,
                cloud_api=runtime.cloud_api,
            )

        run_id = await runtime.db.transaction(runtime.tenant_id, run, label="custodian_sweep")

        async def read_summary(
            cur: psycopg.AsyncCursor,
        ) -> tuple[tuple[Any, ...], dict[str, int]]:
            await cur.execute(
                "SELECT status, skills_run, checks_run, checks_skipped "
                "FROM mnemos.custodian_runs WHERE tenant_id = %s AND run_id = %s",
                (runtime.tenant_id, run_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise RuntimeError(f"run {run_id} vanished before its own summary could be read")
            await cur.execute(
                "SELECT severity, count(*) FROM mnemos.custodian_findings "
                "WHERE tenant_id = %s AND run_id = %s GROUP BY severity",
                (runtime.tenant_id, run_id),
            )
            return row, dict(await cur.fetchall())

        (
            (status, skills_run, checks_run, checks_skipped),
            by_severity,
        ) = await runtime.db.transaction(
            runtime.tenant_id, read_summary, label="read_summary", read_only=True
        )

    print(f"run {run_id}: {status}")
    print(f"  skills_run={skills_run} checks_run={checks_run} checks_skipped={checks_skipped}")
    print(f"  findings: {by_severity or 'none'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="mnemos-custodian", description=__doc__)
    parser.add_argument(
        "--trigger",
        choices=[t.value for t in TriggerSource],
        default=TriggerSource.MANUAL.value,
    )
    parser.add_argument("--detail", default=None, help="free-text trigger detail, e.g. alarm name")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        return asyncio.run(_run(TriggerSource(args.trigger), args.detail))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
