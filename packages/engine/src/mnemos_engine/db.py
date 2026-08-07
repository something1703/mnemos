"""Connection management and the mandatory retry wrapper.

CockroachDB uses SERIALIZABLE isolation, so a transaction that loses a conflict
is aborted with SQLSTATE 40001 and *must* be retried by the client. This is not
an error condition; it is the normal cost of serializability, and code that does
not retry will fail under exactly the concurrency this product is built for.

Every transaction in Mnemos goes through `transaction()`. There is no second
path — `make check` greps for raw transaction usage outside this module.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from .errors import TenantContextMissing

log = logging.getLogger("mnemos.db")

T = TypeVar("T")

SERIALIZATION_FAILURE = "40001"
MAX_ATTEMPTS = 8
BASE_BACKOFF_SECONDS = 0.005
MAX_BACKOFF_SECONDS = 1.0


class Database:
    """Owns the pool and is the only place a transaction may begin."""

    def __init__(self, dsn: str, *, min_size: int = 2, max_size: int = 16) -> None:
        self._dsn = dsn
        self._pool = AsyncConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"autocommit": True},
        )

    async def open(self) -> None:
        await self._pool.open(wait=True, timeout=30)

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        async with self._pool.connection() as conn:
            yield conn

    async def transaction(
        self,
        tenant_id: UUID | None,
        fn: Callable[[psycopg.AsyncCursor], Awaitable[T]],
        *,
        label: str = "txn",
        read_only: bool = False,
        as_of: str | None = None,
    ) -> T:
        """Run `fn` inside one serializable transaction, retrying on 40001.

        `fn` must be **idempotent**: it can be called several times, and any
        state it mutates outside the database will be mutated once per attempt.
        Keep side effects (Bedrock calls, S3 writes, log emission that matters)
        outside the callback.

        `as_of` issues `SET TRANSACTION AS OF SYSTEM TIME <expr>` for temporal
        reads. CockroachDB requires it to be the first statement in the
        transaction, which is why it is a parameter here rather than something a
        caller can bolt on afterwards.
        """
        last_error: psycopg.Error | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            async with self._pool.connection() as conn:
                await conn.set_autocommit(False)
                try:
                    async with conn.cursor() as cur:
                        # AS OF SYSTEM TIME must precede everything else.
                        if as_of is not None:
                            await cur.execute(f"SET TRANSACTION AS OF SYSTEM TIME {as_of}")
                        if read_only:
                            await cur.execute("SET TRANSACTION READ ONLY")
                        if tenant_id is not None:
                            # RLS reads this. SET LOCAL keeps it scoped to the
                            # transaction, so a pooled connection cannot carry
                            # one tenant's context into another's request.
                            await cur.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
                        result = await fn(cur)
                    await conn.commit()
                except psycopg.Error as exc:
                    await conn.rollback()
                    if getattr(exc, "sqlstate", None) != SERIALIZATION_FAILURE:
                        raise
                    last_error = exc
                    if attempt == MAX_ATTEMPTS:
                        break
                    delay = _backoff(attempt)
                    log.info(
                        "40001 retry",
                        extra={"label": label, "attempt": attempt, "delay_s": round(delay, 4)},
                    )
                    await asyncio.sleep(delay)
                    continue
                except Exception:
                    await conn.rollback()
                    raise
                else:
                    if attempt > 1:
                        log.info("40001 resolved", extra={"label": label, "attempts": attempt})
                    return result

        assert last_error is not None
        raise last_error

    async def gc_ttl_seconds(self) -> int:
        """The cluster's MVCC retention, which bounds recall_as_of().

        Read from the live cluster rather than configured, because a stale
        constant here would let temporal recall promise a window the database
        cannot honour.
        """
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT raw_config_sql FROM [SHOW ZONE CONFIGURATION FOR RANGE default]"
            )
            row = await cur.fetchone()
        if not row:
            return 14400
        import re

        match = re.search(r"gc\.ttlseconds\s*=\s*(\d+)", str(row[0]))
        return int(match.group(1)) if match else 14400


def _backoff(attempt: int) -> float:
    """Exponential backoff with full jitter.

    Full jitter rather than a fixed multiplier: retrying contenders that all
    back off by the same amount collide again on the next attempt, which is how
    a brief conflict becomes a sustained one.
    """
    ceiling = min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))
    return random.uniform(0, ceiling)  # noqa: S311 - jitter, not cryptography


def require_tenant(tenant_id: UUID | None) -> UUID:
    """Fail closed when a tenant is missing.

    Without a tenant, RLS returns zero rows — which a caller reads as "nothing
    is known" rather than "you forgot to scope this". Raising makes the bug
    visible where it happened.
    """
    if tenant_id is None:
        raise TenantContextMissing(
            "no tenant bound to this operation; RLS would silently return nothing"
        )
    return tenant_id
