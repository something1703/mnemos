"""MCP server assembly: transport, authentication middleware, tool registration.

Authentication is ASGI middleware rather than an MCP-level concern because the
MCP SDK owns tool dispatch and the credential arrives as an HTTP header. The
middleware resolves `Authorization: Bearer mn_live_...` to a Principal once per
request and binds it to a ContextVar the tools read.

Every rejected request is logged. Denials are security telemetry, not noise to
discard — a spike of 401s against one tenant is exactly the signal worth
having, and it costs nothing to keep.
"""

from __future__ import annotations

import json
import logging

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings
from .context import reset_principal, set_principal
from .keys import AuthError, resolve_key, touch_key
from .runtime import Runtime
from .tools import register_tools

log = logging.getLogger("mnemos.api.server")

SERVER_INSTRUCTIONS = """Mnemos — accountable memory for agents.

Memory here is governed, not just stored. Three things shape how you should
use these tools:

1. Everything you write is UNTRUSTED until something independent corroborates
   it. That is not a bug to route around — `recall` hiding an unverified fact
   is the system working. If results look thin, check `unverified_withheld`
   before concluding nothing is known.

2. Declare what caused your actions. Pass the recall_ids from `recall` into
   `record_action`. That is what lets `explain` reconstruct your reasoning
   later, and what lets a revocation tell someone that a decision rested on
   evidence since withdrawn.

3. Destruction requires the admin scope and an explicit confirm, and can be
   refused outright by a legal hold. Preview first (confirm=false); the
   preview is exact, not an estimate.
"""


def build_server(runtime: Runtime) -> MCPServer:
    server: MCPServer = MCPServer(
        name="mnemos",
        title="Mnemos — Accountable Memory for Agents",
        version="0.1.0",
        instructions=SERVER_INSTRUCTIONS,
    )
    register_tools(server, runtime)
    return server


class AuthMiddleware:
    """Resolve the bearer token to a Principal, or reject before dispatch."""

    def __init__(self, app: ASGIApp, runtime: Runtime) -> None:
        self._app = app
        self._runtime = runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        raw = headers.get("authorization", "")
        token = raw[7:].strip() if raw.lower().startswith("bearer ") else ""
        path = scope.get("path", "")

        if not token:
            log.warning("auth: missing bearer token path=%s", path)
            await _reject(send, 401, "missing Authorization: Bearer mn_live_...")
            return

        try:
            principal = await self._runtime.db.transaction(
                None,
                lambda cur: resolve_key(cur, token),
                label="resolve_key",
            )
        except AuthError as exc:
            log.warning("auth: rejected path=%s status=%s reason=%s", path, exc.status, exc)
            await _reject(send, exc.status, str(exc))
            return

        marker = set_principal(principal)
        try:
            # Best-effort telemetry; must never fail the request it describes.
            try:
                await self._runtime.db.transaction(
                    principal.tenant_id,
                    lambda cur: touch_key(cur, principal.key_id, principal.tenant_id),
                    label="touch_key",
                )
            except Exception:
                log.debug("auth: could not record last_used_at", exc_info=True)

            await self._app(scope, receive, send)
        finally:
            reset_principal(marker)


def transport_security_for(settings: Settings) -> TransportSecuritySettings:
    """DNS rebinding protection, configured rather than switched off.

    The SDK's default allow-list is loopback only, so a deployed server answers
    every MCP request with 421 "Invalid Host header" until its own hostname is
    named. That is the right default and the wrong thing to fix by disabling
    the middleware: the check costs nothing and closes a real attack on any
    developer running this locally.

    Origins are left unrestricted. Authorisation here is a bearer token that a
    browser will not attach on its own, so an Origin allow-list would add
    ceremony without adding a control.
    """
    allowed = list(settings.allowed_hosts)
    if "*" in allowed:
        log.warning(
            "MNEMOS_ALLOWED_HOSTS contains '*': DNS rebinding protection is DISABLED "
            "for the MCP transport."
        )
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed)


def with_auth(app: ASGIApp, runtime: Runtime) -> ASGIApp:
    return AuthMiddleware(app, runtime)


def with_slash_alias(app: ASGIApp, prefix: str) -> ASGIApp:
    """Make a bare ``/mcp`` behave as ``/mcp/``.

    Starlette's Mount only matches the prefix followed by a slash, so a bare
    ``POST /mcp`` falls past the MCP mount to the REST app and returns a plain
    404 with no hint that a trailing slash was the problem. Every client config
    anyone will paste says ``.../mcp``, so the bare form has to work.

    This wraps the whole app rather than the mounted one: by the time the
    router is choosing between mounts it has already declined ``/mcp``, so the
    rewrite has to happen before routing.

    A redirect would also work, but 307 costs a second Lambda invocation per
    request and not every MCP client re-sends the body on redirect. Rewriting
    the path is one dictionary assignment and has neither problem.
    """
    target = prefix + "/"

    async def normalised(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == prefix:
            scope = {**scope, "path": target, "raw_path": target.encode()}
        await app(scope, receive, send)

    return normalised


async def _reject(send: Send, status: int, message: str) -> None:
    """Emit a JSON error directly as ASGI messages.

    Constructed by hand rather than through a Response object: this runs before
    any application framework is involved, and hand-rolling two messages is
    less indirection than borrowing a response class to do the same thing.
    """
    body = json.dumps({"error": message}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
