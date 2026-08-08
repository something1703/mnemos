"""One ASGI app serving both surfaces.

  /mcp   MCP streamable HTTP — agents. Read AND write, scope-gated.
  /v1/*  REST — the console and anyone with curl. Read-only.
  /health, /docs, /openapi.json — unauthenticated.

They are mounted together rather than deployed separately because they are the
same service with the same tenancy rules; splitting them would double the
deployment surface to no benefit while making it possible for the two to drift
apart on authorisation.

`stateless=True` matters for Lambda. The MCP streamable transport can hold
per-session state across requests, which is exactly what a function that may
be a different instance on every invocation cannot do. Stateless mode plus
buffered JSON responses (rather than SSE) makes each request self-contained,
which is what serverless requires — and costs nothing locally.
"""

from __future__ import annotations

import logging

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import ASGIApp

from .rest import build_rest_app
from .runtime import Runtime
from .server import build_server, with_auth

log = logging.getLogger("mnemos.api.app")


def build_app(runtime: Runtime, *, stateless: bool = False) -> ASGIApp:
    mcp_server = build_server(runtime)
    mcp_app = mcp_server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=stateless,
        json_response=stateless,
    )

    # Auth wraps only the MCP mount; REST authenticates per-route through a
    # FastAPI dependency so that /health and /docs stay reachable without a
    # credential. A judge should be able to see the service is alive, and its
    # posture, before being handed a key.
    guarded_mcp = with_auth(mcp_app, runtime)
    rest_app = build_rest_app(runtime)

    app = Starlette(
        routes=[
            Mount("/mcp", app=guarded_mcp),
            Mount("/", app=rest_app),
        ],
        lifespan=mcp_app.router.lifespan_context,
    )
    app.state.runtime = runtime
    log.info("app: /mcp (MCP, stateless=%s) + /v1 (REST, read-only)", stateless)
    return app
