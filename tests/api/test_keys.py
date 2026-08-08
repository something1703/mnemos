"""API keys: minting, resolution, scope ordering, and failure uniformity."""

from __future__ import annotations

import uuid

import pytest
from mnemos_api.keys import (
    KEY_PREFIX,
    AuthError,
    Principal,
    Scope,
    hash_key,
    mint_key,
    resolve_key,
)

pytestmark = pytest.mark.security


# ------------------------------------------------------------------ scopes


def test_scope_ordering_is_the_authorisation_check() -> None:
    """The whole permission model is `granted >= required`, so the ordering
    IS the security property — worth asserting directly rather than trusting
    that an enum happens to sort correctly."""
    assert Scope.READ < Scope.WRITE < Scope.ADMIN

    admin = Principal(uuid.uuid4(), uuid.uuid4(), Scope.ADMIN, "a")
    write = Principal(uuid.uuid4(), uuid.uuid4(), Scope.WRITE, "w")
    read = Principal(uuid.uuid4(), uuid.uuid4(), Scope.READ, "r")

    assert admin.can(Scope.READ) and admin.can(Scope.WRITE) and admin.can(Scope.ADMIN)
    assert write.can(Scope.READ) and write.can(Scope.WRITE)
    assert not write.can(Scope.ADMIN)
    assert read.can(Scope.READ)
    assert not read.can(Scope.WRITE)
    assert not read.can(Scope.ADMIN)


def test_scope_parse_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="unknown scope"):
        Scope.parse("superuser")


# ------------------------------------------------------------------ minting


async def test_minted_key_resolves_to_its_principal(runtime, tenant) -> None:
    async def run(cur):
        return await mint_key(cur, tenant, scope=Scope.WRITE, label="ci")

    plaintext, key_id = await runtime.db.transaction(tenant, run, label="mint")
    assert plaintext.startswith(KEY_PREFIX)

    async def resolve(cur):
        return await resolve_key(cur, plaintext)

    principal = await runtime.db.transaction(tenant, resolve, label="resolve")
    assert principal.tenant_id == tenant
    assert principal.key_id == key_id
    assert principal.scope is Scope.WRITE
    assert principal.label == "ci"


async def test_plaintext_is_never_stored(runtime, tenant) -> None:
    """A database dump must not yield working credentials. Only the SHA-256
    is persisted, so the stored value cannot be replayed as a key."""

    async def run(cur):
        return await mint_key(cur, tenant, scope=Scope.READ, label="dump-test")

    plaintext, key_id = await runtime.db.transaction(tenant, run, label="mint")

    async def read(cur):
        await cur.execute(
            "SELECT key_hash FROM mnemos.api_keys WHERE tenant_id = %s AND key_id = %s",
            (tenant, key_id),
        )
        return bytes((await cur.fetchone())[0])

    stored = await runtime.db.transaction(tenant, read, label="read")
    assert stored == hash_key(plaintext)
    assert plaintext.encode() not in stored


async def test_two_mints_never_collide(runtime, tenant) -> None:
    async def run(cur):
        return await mint_key(cur, tenant, scope=Scope.READ, label="x")

    first, _ = await runtime.db.transaction(tenant, run, label="mint")
    second, _ = await runtime.db.transaction(tenant, run, label="mint")
    assert first != second


# --------------------------------------------------------------- rejection


@pytest.mark.parametrize(
    "presented",
    ["", "   ", "not-a-key", "Bearer mn_live_x", "mn_test_abc", f"{KEY_PREFIX}wrong"],
)
async def test_bad_keys_are_rejected(runtime, tenant, presented: str) -> None:
    async def resolve(cur):
        return await resolve_key(cur, presented)

    with pytest.raises(AuthError):
        await runtime.db.transaction(tenant, resolve, label="resolve")


async def test_revoked_key_stops_working(runtime, tenant) -> None:
    async def run(cur):
        return await mint_key(cur, tenant, scope=Scope.ADMIN, label="to-revoke")

    plaintext, key_id = await runtime.db.transaction(tenant, run, label="mint")

    async def revoke(cur):
        await cur.execute(
            "UPDATE mnemos.api_keys SET revoked_at = now() WHERE tenant_id = %s AND key_id = %s",
            (tenant, key_id),
        )

    await runtime.db.transaction(tenant, revoke, label="revoke")

    async def resolve(cur):
        return await resolve_key(cur, plaintext)

    with pytest.raises(AuthError):
        await runtime.db.transaction(tenant, resolve, label="resolve")


async def test_failure_messages_do_not_distinguish_failure_modes(runtime, tenant) -> None:
    """An unknown key and a revoked key must look identical to the caller.

    Telling an attacker which of their guesses corresponded to a real key is
    free information, and the only party inconvenienced by hiding it is the
    attacker.
    """

    async def mint(cur):
        return await mint_key(cur, tenant, scope=Scope.READ, label="revoked")

    revoked_plaintext, key_id = await runtime.db.transaction(tenant, mint, label="mint")

    async def revoke(cur):
        await cur.execute(
            "UPDATE mnemos.api_keys SET revoked_at = now() WHERE tenant_id = %s AND key_id = %s",
            (tenant, key_id),
        )

    await runtime.db.transaction(tenant, revoke, label="revoke")

    messages = set()
    for candidate in (revoked_plaintext, f"{KEY_PREFIX}definitely-not-real"):

        async def resolve(cur, c=candidate):
            return await resolve_key(cur, c)

        with pytest.raises(AuthError) as exc:
            await runtime.db.transaction(tenant, resolve, label="resolve")
        messages.add(str(exc.value))

    assert len(messages) == 1, f"failure modes are distinguishable: {messages}"
