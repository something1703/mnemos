"""Per-request caller identity.

A `ContextVar` rather than a parameter threaded through every tool, because
the MCP SDK owns the tool-invocation path and does not hand us a place to put
it. ContextVars are the correct primitive here: they are task-local under
asyncio, so two concurrent requests on the same worker cannot observe each
other's principal — which for a multi-tenant memory system is not a detail.

`current_principal()` raises rather than returning None when unset. A tool
that ran with no authenticated caller would fall through to whatever tenant
scoping happened to be in place, and failing closed is the only acceptable
behaviour at that boundary.
"""

from __future__ import annotations

from contextvars import ContextVar

from .keys import AuthError, Principal

_principal: ContextVar[Principal | None] = ContextVar("mnemos_principal", default=None)


def set_principal(principal: Principal | None) -> object:
    """Bind the caller for this task. Returns a token for `reset_principal`."""
    return _principal.set(principal)


def reset_principal(token: object) -> None:
    """Restore the previous principal.

    Tolerates a token minted in a different context. That happens legitimately
    when a sync caller binds a principal and an async task later unbinds it —
    the token is then not resettable, but clearing the variable achieves the
    same end state, which is what actually matters at a security boundary.
    """
    try:
        _principal.reset(token)  # type: ignore[arg-type]
    except ValueError:
        _principal.set(None)


def clear_principal() -> None:
    """Unbind unconditionally. Used by teardown paths that only care that no
    principal survives into the next request."""
    _principal.set(None)


def current_principal() -> Principal:
    principal = _principal.get()
    if principal is None:
        raise AuthError("no authenticated caller for this request")
    return principal
