"""API keys and scopes — where invariant 1 is enforced against *callers*.

The Warden enforces that destruction is deterministic and model-free. This
module enforces the complementary half: that an agent holding an ordinary
write key cannot reach destruction at all, regardless of what it asks for.

Three properties worth stating explicitly, because each is a real decision:

* **Keys are stored hashed.** The plaintext `mn_live_...` exists exactly once,
  at mint time, and is unrecoverable afterwards — the same discipline the
  CockroachDB Cloud console applies to SQL passwords. A leaked database dump
  therefore does not yield working credentials.
* **Lookup is by hash, in constant time.** We look the key up by its SHA-256
  (which has a unique index), then compare with `secrets.compare_digest`. The
  index lookup is what makes it fast; the constant-time compare is what stops
  the comparison itself leaking information.
* **Scopes are ordered, and destruction is not merely "write".** read < write
  < admin. `forget`, `revoke_source`, and `set_legal_hold` require admin.
  Treating erasure as just another write is the mistake this ordering exists
  to prevent.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

import psycopg

KEY_PREFIX = "mn_live_"
_TOKEN_BYTES = 32


class Scope(IntEnum):
    """Ordered so `granted >= required` is the whole check.

    IntEnum rather than StrEnum precisely because the ordering is the
    security property; a string comparison would silently do the wrong thing.
    """

    READ = 10
    WRITE = 20
    ADMIN = 30

    @property
    def label(self) -> str:
        return self.name.lower()

    @classmethod
    def parse(cls, raw: str) -> Scope:
        try:
            return cls[raw.strip().upper()]
        except KeyError:
            raise ValueError(f"unknown scope {raw!r}; expected read, write, or admin") from None


@dataclass(frozen=True)
class Principal:
    """Who is calling, resolved from a presented key."""

    tenant_id: UUID
    key_id: UUID
    scope: Scope
    label: str

    def can(self, required: Scope) -> bool:
        return self.scope >= required


class AuthError(Exception):
    """Authentication or authorisation failure.

    Carries an HTTP status so the transport layer does not have to re-derive
    it, and deliberately does NOT distinguish "no such key" from "revoked key"
    in its public message — telling an attacker which of their guesses was a
    real key is free information.
    """

    def __init__(self, message: str, *, status: int = 401) -> None:
        self.status = status
        super().__init__(message)


def hash_key(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


def generate_key() -> str:
    return f"{KEY_PREFIX}{secrets.token_urlsafe(_TOKEN_BYTES)}"


async def mint_key(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    scope: Scope,
    label: str,
) -> tuple[str, UUID]:
    """Create a key and return (plaintext, key_id).

    The plaintext is returned exactly once and never stored. A caller that
    loses it must mint another — which is the correct trade, because the
    alternative is a database that hands out working credentials to anyone who
    can read it.
    """
    plaintext = generate_key()
    await cur.execute(
        "INSERT INTO mnemos.api_keys (tenant_id, key_hash, label, scope) "
        "VALUES (%s, %s, %s, %s) RETURNING key_id",
        (tenant_id, hash_key(plaintext), label, scope.label),
    )
    row = await cur.fetchone()
    assert row is not None
    return plaintext, row[0]


async def resolve_key(cur: psycopg.AsyncCursor, presented: str) -> Principal:
    """Resolve a presented key to a Principal, or raise AuthError.

    Note the deliberate uniformity of the failure messages: a malformed key, an
    unknown key, and a revoked key all produce the same "invalid API key". The
    caller learns only that it failed.
    """
    presented = (presented or "").strip()
    if not presented.startswith(KEY_PREFIX):
        raise AuthError("invalid API key")

    digest = hash_key(presented)
    await cur.execute(
        "SELECT tenant_id, key_id, scope, label, key_hash, revoked_at "
        "FROM mnemos.api_keys WHERE key_hash = %s",
        (digest,),
    )
    row = await cur.fetchone()
    if row is None:
        raise AuthError("invalid API key")

    tenant_id, key_id, scope_raw, label, stored_hash, revoked_at = row

    # The index lookup already matched, so this is belt-and-braces against a
    # future change that makes the lookup non-exact (a prefix index, a cache).
    if not secrets.compare_digest(bytes(stored_hash), digest):
        raise AuthError("invalid API key")

    if revoked_at is not None:
        raise AuthError("invalid API key")

    return Principal(
        tenant_id=tenant_id,
        key_id=key_id,
        scope=Scope.parse(scope_raw),
        label=label,
    )


async def touch_key(cur: psycopg.AsyncCursor, key_id: UUID, tenant_id: UUID) -> None:
    """Record last use. Best-effort telemetry, never a gate on the request."""
    await cur.execute(
        "UPDATE mnemos.api_keys SET last_used_at = now() WHERE tenant_id = %s AND key_id = %s",
        (tenant_id, key_id),
    )


def require(principal: Principal, needed: Scope, operation: str) -> None:
    """Authorise, or raise a 403 that names what was missing.

    The message states the required scope on purpose. Hiding it does not slow
    an attacker down (they can enumerate by trying), and it does waste a
    legitimate integrator's afternoon.
    """
    if not principal.can(needed):
        raise AuthError(
            f"{operation} requires the '{needed.label}' scope; "
            f"this key has '{principal.scope.label}'",
            status=403,
        )
