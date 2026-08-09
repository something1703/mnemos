"""AWS Lambda entry point for the sleep cycle.

Unlike the API (`services/api/.../lambda_handler.py`), this is not an ASGI
app behind Mangum — the sleep cycle has no HTTP surface at all. It is invoked
directly, either by EventBridge on a schedule or by a Step Functions Task
state, with a small JSON event choosing which stage to run:

    {"stage": "consolidate"}              # hourly light run (batch_limit default)
    {"stage": "consolidate", "limit": 5}  # explicit override
    {"stage": "decay"}                    # weekly sweep
    {"stage": "checkpoint"}               # hourly Merkle checkpoint
    {"stage": "cycle"}                    # consolidation then checkpoint, nightly

Secrets are resolved from Secrets Manager at cold start, same pattern and
same reasoning as the API's handler: a database URL sitting in a plaintext
Lambda environment variable is visible to anyone holding
lambda:GetFunctionConfiguration, which is a wider audience than it looks.

The runtime (connection pool, embedder, chat client) is built once per
execution environment and kept warm across invocations — opening a fresh
pool on every scheduled run would mean a new TCP and TLS handshake to
CockroachDB every time, for a job that runs hourly at minimum.

**A plain `asyncio.run()` per invocation would silently break that reuse.**
`asyncio.run()` opens a new event loop and closes it when the call returns;
`psycopg_pool.AsyncConnectionPool` binds internal asyncio primitives (locks,
the pool's own worker tasks) to the loop it was opened under. Cache the pool
across invocations but keep calling `asyncio.run()` and invocation two hands
the pool a loop that is already closed — "attached to a different loop"
errors, or a hang. This is the same class of bug the API's Lambda handler
hit with Mangum re-running the ASGI lifespan per request (see `services/api/
src/mnemos_api/asgi.py`); the fix here is the same shape: keep ONE event loop
alive at module scope for the life of the execution environment, and drive
every invocation through it with `run_until_complete` instead of `run`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logging.basicConfig(level=os.environ.get("MNEMOS_LOG_LEVEL", "INFO"))
log = logging.getLogger("mnemos.sleep_cycle.lambda")


def _load_secrets() -> None:
    """Hydrate os.environ from Secrets Manager, before settings are read.

    Runs at import time (cold start), so the cost is paid once per execution
    environment. Existing environment variables win, which makes a local
    override possible without editing the secret.
    """
    arn = os.environ.get("MNEMOS_SECRET_ARN")
    if not arn:
        log.info("MNEMOS_SECRET_ARN not set; using environment variables as-is")
        return

    import boto3

    client = boto3.client("secretsmanager")
    payload = client.get_secret_value(SecretId=arn)["SecretString"]
    loaded = 0
    for key, value in json.loads(payload).items():
        if key not in os.environ:
            os.environ[key] = str(value)
            loaded += 1
    log.info("loaded %d value(s) from Secrets Manager", loaded)


_load_secrets()

from .cli import run_checkpoint, run_consolidation, run_decay  # noqa: E402
from .runtime import Runtime, build_runtime  # noqa: E402

_runtime: Runtime | None = None
_loop: asyncio.AbstractEventLoop | None = None


def _emit_consolidation_heartbeat() -> None:
    """One data point per successful consolidation, so PHASE_07's
    "consolidation lag" alarm (infra/sleep-cycle's own — a dead man's switch,
    not a threshold on any value this service computes about itself) has
    something real to watch: `TreatMissingData=breaching` over a 6-hour
    window means the alarm fires when consolidation has silently stopped
    running, not just when it errors (already covered by
    mnemos-sleep-cycle-errors). Best-effort — a failed metric write must
    never fail the consolidation it is reporting on.
    """
    try:
        import boto3

        boto3.client("cloudwatch").put_metric_data(
            Namespace="Mnemos/SleepCycle",
            MetricData=[{"MetricName": "ConsolidationHeartbeat", "Value": 1, "Unit": "Count"}],
        )
    except Exception:
        log.warning("failed to emit consolidation heartbeat metric", exc_info=True)


def _get_loop() -> asyncio.AbstractEventLoop:
    """One event loop per execution environment, not per invocation — see the
    module docstring for why `asyncio.run()` here would silently break pool
    reuse on the second warm invocation."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop


async def _get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = await build_runtime()
    return _runtime


async def _dispatch(event: dict[str, Any]) -> dict[str, Any]:
    stage = event.get("stage", "cycle")
    runtime = await _get_runtime()

    if stage == "consolidate":
        result = await run_consolidation(runtime, limit=event.get("limit"))
        _emit_consolidation_heartbeat()
        return result
    if stage == "decay":
        return await run_decay(runtime)
    if stage == "checkpoint":
        return await run_checkpoint(runtime)
    if stage == "cycle":
        consolidation = await run_consolidation(runtime)
        _emit_consolidation_heartbeat()
        checkpoint = await run_checkpoint(runtime)
        return {"consolidation": consolidation, "checkpoint": checkpoint}

    raise ValueError(f"unknown stage {stage!r}; expected consolidate|decay|checkpoint|cycle")


def handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """The Lambda entry point.

    Drives every invocation through the SAME event loop (`_get_loop`), which
    is what keeps `_runtime`'s connection pool valid across a warm
    container's invocations rather than bound to a loop that closed when the
    previous invocation's `asyncio.run()` would have returned.
    """
    stage = event.get("stage", "cycle")
    log.info("invoked: stage=%s", stage)
    loop = _get_loop()
    try:
        result = loop.run_until_complete(_dispatch(event))
    except Exception:
        log.exception("stage %s failed", stage)
        raise
    log.info("stage %s complete: %s", stage, result)
    return {"stage": stage, "result": result}
