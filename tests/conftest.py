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
    """
    with admin_conn.cursor() as cur:
        for role, user in ROLE_USERS.items():
            cur.execute(f"CREATE USER IF NOT EXISTS {user}")
            cur.execute(f"GRANT {role} TO {user}")
            cur.execute(f"GRANT CONNECT ON DATABASE mnemos TO {user}")


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
    shard_id: int = 0,
) -> uuid.UUID:
    """Write a real audit row and arm its ticket for the current transaction.

    This mirrors what ``mnemos_engine.append_audit`` will do in Phase 03: insert
    into the chain, then ``SET LOCAL app.audit_ticket`` so the trigger on the
    protected tables can resolve it. Callers must already be inside a
    transaction — that is the point.
    """
    ticket = uuid.uuid4()
    # Every real caller sets the tenant context; RLS (FORCE) blocks the audit
    # insert otherwise for any role without BYPASSRLS. root bypasses RLS, so
    # omitting this would pass as admin and fail as the Warden — exactly the
    # kind of divergence that hides a bug until deployment.
    cur.execute(f"SET LOCAL app.tenant_id = '{tenant_id}'")
    cur.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM mnemos.audit_chain "
        "WHERE tenant_id = %s AND shard_id = %s",
        (tenant_id, shard_id),
    )
    row = cur.fetchone()
    seq = row[0] if row else 1
    cur.execute(
        """
        INSERT INTO mnemos.audit_chain
            (tenant_id, shard_id, seq, ticket, op, subject_key, actor,
             payload, payload_hash, prev_hash, entry_hash)
        VALUES (%s, %s, %s, %s, %s, %s, 'test', '{}'::JSONB, %s, %s, %s)
        """,
        (tenant_id, shard_id, seq, ticket, op, subject_key, b"\x00", b"\x00", b"\x00"),
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
