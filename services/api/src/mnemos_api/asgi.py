"""A self-constructing ASGI app, for environments that hand us no place to
run async setup before the server starts.

`build_app(runtime)` needs a Runtime that has already opened its pools, which
suits `mnemos-api serve` (it can await the construction). Lambda cannot: the
handler is imported synchronously, and `psycopg_pool` needs a running event
loop to open a pool. So the runtime is built inside the ASGI lifespan, which
is the one place a server guarantees an event loop before the first request.

**Mangum runs the ASGI lifespan once per invocation, not once per container.**
That single fact drives the shape of this module, and getting it wrong is not
subtle: the first deployed request succeeded and every later one failed with
`PoolClosed: the pool is already closed`, because the shutdown half of the
first request's lifespan had closed the pool the second request went on to
use. Routes appended during startup accumulated the same way — each invocation
added another mount to the same router, and the stale one matched first.

So the pieces are split by how long each can safely live:

  * The **runtime** (connection pools, embedder, Warden) is built once per
    execution environment and deliberately never closed. Lambda freezes and
    eventually kills the environment; that is the teardown. Reopening pools
    per request would also mean a fresh TCP and TLS handshake to CockroachDB
    on every call, which is most of a warm request's budget.
  * The **MCP transport** is rebuilt each lifespan, because its session
    manager may only be run once and holds an anyio task group that cannot
    outlive the task that entered it.
  * Routes are assembled into a **fresh inner app** each lifespan rather than
    appended to a long-lived router, so re-entry cannot accumulate mounts.

The pool is also kept small (see `Settings.db_pool_max`): each concurrent
execution environment holds its own, and a pool sized for one long-lived
server multiplied by serverless concurrency is how a deployment exhausts its
own database.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import get_settings
from .rest import build_rest_app
from .runtime import Runtime, build_runtime
from .server import build_server, transport_security_for, with_auth, with_slash_alias

log = logging.getLogger("mnemos.api.asgi")

# Per-execution-environment singletons. Module scope is the correct lifetime
# here: it is exactly as long as the warm container lives.
_runtime: Runtime | None = None
_rest_app: ASGIApp | None = None
_lock = asyncio.Lock()


async def _shared_runtime() -> tuple[Runtime, ASGIApp]:
    """Build the runtime and REST app once, then hand back the same pair.

    Double-checked under a lock rather than built at import: two requests can
    arrive before the first finishes initialising, and opening two pools would
    leak the loser.
    """
    global _runtime, _rest_app
    if _runtime is not None and _rest_app is not None:
        return _runtime, _rest_app
    async with _lock:
        if _runtime is None or _rest_app is None:
            settings = get_settings()
            _runtime = await build_runtime(settings)
            _rest_app = build_rest_app(_runtime)
            log.info("runtime built for this execution environment")
        return _runtime, _rest_app


def create_app(*, stateless: bool | None = None) -> ASGIApp:
    """Build an app that constructs its own Runtime during startup.

    `stateless` defaults to on when running under Lambda, detected via
    AWS_LAMBDA_FUNCTION_NAME. Getting this wrong on Lambda does not fail
    loudly — it fails as intermittent "session not found" errors when a
    follow-up request lands on a different instance — so the default is
    derived from the environment rather than left to whoever writes the
    deployment config.
    """
    if stateless is None:
        stateless = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

    current: dict[str, ASGIApp] = {}

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        runtime, rest_app = await _shared_runtime()

        mcp_app = build_server(runtime).streamable_http_app(
            streamable_http_path="/",
            stateless_http=stateless,
            json_response=stateless,
            transport_security=transport_security_for(runtime.settings),
        )
        current["app"] = Starlette(
            routes=[
                Mount("/mcp", app=with_auth(mcp_app, runtime)),
                Mount("/", app=rest_app),
            ]
        )

        # The transport's session manager runs inside its own lifespan. It is
        # scoped to this cycle on purpose — see the module docstring.
        async with mcp_app.router.lifespan_context(mcp_app):
            try:
                yield
            finally:
                current.pop("app", None)

    async def dispatch(scope: Scope, receive: Receive, send: Send) -> None:
        app = current.get("app")
        if app is None:  # pragma: no cover - only if a server skips lifespan
            raise RuntimeError(
                "request arrived before ASGI lifespan startup completed; "
                "the server must run the lifespan protocol"
            )
        await app(scope, receive, send)

    outer: Any = Starlette(lifespan=lifespan, routes=[Mount("/", app=dispatch)])
    return with_slash_alias(outer, "/mcp")
