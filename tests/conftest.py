"""Shared fixtures for the invariant and schema suites.

These run against the local Docker cluster (`make db-local` then
`make db-migrate`). They are not mocked: an invariant proven against a fake
database proves nothing, because the whole claim is that CockroachDB itself
enforces these rules.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from mnemos_engine.canonical import GENESIS_HASH, entry_hash, payload_hash
from mnemos_engine.canonical import shard_for as canonical_shard_for
from psycopg.types.json import Json

LOCAL_URL = "postgresql://root@localhost:26257/mnemos?sslmode=disable"

# Login users created once per session, each granted exactly one service role.
# Invariant 1 is tested by *being* the API and failing to delete, rather than by
# reading a grant table and trusting our own interpretation of it.
ROLE_USERS = {
    "mnemos_api": "test_api_user",
    "mnemos_pipeline": "test_pipeline_user",
    "mnemos_readonly": "test_readonly_user",
    "mnemos_warden": "test_warden_user",
}


@pytest.fixture(scope="session")
def db_url() -> str:
    return os.environ.get("MNEMOS_DB_URL_LOCAL", LOCAL_URL)


@pytest.fixture(scope="session")
def admin_conn(db_url: str) -> Iterator[psycopg.Connection]:
    try:
        conn = psycopg.connect(db_url, autocommit=True, connect_timeout=10)
    except psycopg.OperationalError as exc:
        pytest.skip(f"local CockroachDB unavailable ({exc}). Run: make db-local && make db-migrate")
    with conn:
        yield conn


@pytest.fixture(scope="session", autouse=True)
def _provision_role_users(admin_conn: psycopg.Connection) -> None:
    """Create one login user per service role.

    The local cluster runs insecure, so no password is needed to connect — which
    keeps credentials out of the test suite entirely.

    `mnemos_pipeline` carries `BYPASSRLS` (migration 011), but that attribute
    does NOT propagate through `GRANT role TO user` the way ordinary table
    privileges do — CockroachDB matches real PostgreSQL semantics here, where
    LOGIN/SUPERUSER/BYPASSRLS/CREATEDB/CREATEROLE are attributes of the role
    itself, not privileges inherited via membership. A login granted only
    `mnemos_pipeline` sees zero rows on any cross-tenant query and fails
    silently — no error, just an empty result that reads as "nothing to do"
    instead of "wrong permissions". Found against the real deployed pipeline
    role, not in this suite (nothing here happened to run a cross-tenant query
    as this specific login), which is exactly why it is granted directly here
    too, alongside the regression test in test_deployment_surface.py::
    test_pipeline_role_bypasses_rls_directly_not_only_via_membership.
    """
    with admin_conn.cursor() as cur:
        for role, user in ROLE_USERS.items():
            cur.execute(f"CREATE USER IF NOT EXISTS {user}")
            cur.execute(f"GRANT {role} TO {user}")
            cur.execute(f"GRANT CONNECT ON DATABASE mnemos TO {user}")
            if role == "mnemos_pipeline":
                cur.execute(f"ALTER USER {user} BYPASSRLS")


@pytest.fixture
def txn_conn(db_url: str) -> Iterator[psycopg.Connection]:
    """A fresh connection with autocommit OFF, for tests that drive transactions.

    Separate from ``admin_conn`` on purpose: toggling autocommit on a shared
    session connection fails once a transaction has been aborted, and every
    invariant-2 test deliberately aborts one.
    """
    conn = psycopg.connect(db_url, autocommit=False, connect_timeout=10)
    with conn:
        yield conn


@pytest.fixture
def role_conn(db_url: str):
    """Connect as the login user holding a given service role."""
    opened: list[psycopg.Connection] = []

    def _connect(role: str, *, autocommit: bool = True) -> psycopg.Connection:
        user = ROLE_USERS[role]
        url = db_url.replace("//root@", f"//{user}@")
        conn = psycopg.connect(url, autocommit=autocommit, connect_timeout=10)
        opened.append(conn)
        return conn

    yield _connect
    for conn in opened:
        conn.close()


@pytest.fixture
def tenant(admin_conn: psycopg.Connection) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    with admin_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mnemos.tenants (tenant_id, slug, display_name) VALUES (%s, %s, %s)",
            (tenant_id, f"t-{tenant_id.hex[:8]}", "Test tenant"),
        )
    return tenant_id


def append_audit(
    cur: psycopg.Cursor,
    tenant_id: uuid.UUID,
    *,
    op: str = "remember",
    subject_key: str = "subject:test",
    shard_id: int | None = None,
) -> uuid.UUID:
    """Write a real, VERIFIABLE audit row and arm its ticket.

    Byte-identical to ``mnemos_engine.ledger.append_audit``: canonical payload
    hashing, prev_hash chaining, and chain_heads maintenance. Callers must
    already be inside a transaction — that is the point.

    An earlier version wrote ``payload='{}'`` with placeholder hashes, which
    was invisible to the invariant tests (they assert that BAD writes are
    rejected, not that hashes recompute) but silently corrupted the chain of
    any tenant a test touched. It was caught only when a test that bulk-loads
    into the seeded `clinic` tenant left that tenant's ledger reporting BROKEN
    through the REST verifier — i.e. by a demo surface, not by a test.

    The lesson is the same one db/seed.py already learned: anything that
    appends to the chain must produce entries that verify, or it is not
    exercising the system, it is damaging it.
    """
    ticket = uuid.uuid4()
    shard = shard_id if shard_id is not None else canonical_shard_for(subject_key, 16)

    # Every real caller sets the tenant context; RLS (FORCE) blocks the audit
    # insert otherwise for any role without BYPASSRLS. root bypasses RLS, so
    # omitting this would pass as admin and fail as the Warden — exactly the
    # kind of divergence that hides a bug until deployment.
    cur.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")

    cur.execute(
        "SELECT seq, entry_hash FROM mnemos.chain_heads "
        "WHERE tenant_id = %s AND shard_id = %s FOR UPDATE",
        (tenant_id, shard),
    )
    head = cur.fetchone()
    if head is None:
        # Tolerate a chain written before chain_heads existed, the same way
        # db/seed.py does: repair rather than collide with a duplicate seq.
        cur.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM mnemos.audit_chain "
            "WHERE tenant_id = %s AND shard_id = %s",
            (tenant_id, shard),
        )
        orphan = cur.fetchone()
        prev_seq, prev_hash = (int(orphan[0]) if orphan else 0), GENESIS_HASH
    else:
        prev_seq, prev_hash = int(head[0]), bytes(head[1])

    seq = prev_seq + 1
    body = {
        "op": op,
        "actor": "test",
        "subject_key": subject_key,
        "reason": None,
        "seq": seq,
        "shard_id": shard,
        "tenant_id": str(tenant_id),
        "data": {},
    }
    digest = payload_hash(body)
    entry = entry_hash(digest, prev_hash)

    cur.execute(
        """
        INSERT INTO mnemos.audit_chain
            (tenant_id, shard_id, seq, ticket, op, subject_key, actor, reason,
             payload, payload_hash, prev_hash, entry_hash)
        VALUES (%s, %s, %s, %s, %s, %s, 'test', NULL, %s, %s, %s, %s)
        """,
        (tenant_id, shard, seq, ticket, op, subject_key, Json(body), digest, prev_hash, entry),
    )

    if head is None:
        cur.execute(
            "INSERT INTO mnemos.chain_heads (tenant_id, shard_id, seq, entry_hash) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (tenant_id, shard_id) DO UPDATE SET seq = %s, entry_hash = %s",
            (tenant_id, shard, seq, entry, seq, entry),
        )
    else:
        cur.execute(
            "UPDATE mnemos.chain_heads SET seq = %s, entry_hash = %s, updated_at = now() "
            "WHERE tenant_id = %s AND shard_id = %s",
            (seq, entry, tenant_id, shard),
        )

    cur.execute(f"SET LOCAL app.audit_ticket = '{ticket}'")
    return ticket


def insert_episode(cur: psycopg.Cursor, tenant_id: uuid.UUID, subject_key: str) -> uuid.UUID:
    event_id = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO mnemos.episodic_events
            (tenant_id, subject_key, event_id, home_region, session_id, event_type,
             content_ciphertext, content_dek_wrapped, content_hash, source_trust)
        VALUES (%s, %s, %s, 'us-east-1', %s, 'note', %s, %s, %s, 'operator')
        """,
        (tenant_id, subject_key, event_id, uuid.uuid4(), b"\x00", b"\x00", b"\x00"),
    )
    return event_id


def insert_fact(
    cur: psycopg.Cursor,
    tenant_id: uuid.UUID,
    subject_key: str,
    *,
    trust: str = "unverified",
) -> uuid.UUID:
    fact_id = uuid.uuid4()
    cur.execute(
        """
        INSERT INTO mnemos.semantic_facts
            (tenant_id, fact_id, home_region, subject_key, fact_kind,
             text_ciphertext, text_dek_wrapped, text_hash, trust)
        VALUES (%s, %s, 'us-east-1', %s, 'note', %s, %s, %s, %s)
        """,
        (tenant_id, fact_id, subject_key, b"\x00", b"\x00", b"\x00", trust),
    )
    return fact_id
