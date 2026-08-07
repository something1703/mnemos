"""Phase 02.6 / 10.2 — adversarial tenant isolation.

These run as `mnemos_api` against seeded data and try to cross the tenant
boundary through every surface the API exposes. Requires:

    make db-local && make db-migrate && make db-seed

The interesting case is vector search. An approximate-nearest-neighbour index
that is not partitioned per tenant will happily return another tenant's
neighbours, and the WHERE clause filters them only *after* the index has already
leaked which vectors were close. Our index is prefix-scoped by tenant_id
(migration 003) so the partitioning happens inside the index itself.

Honest scope, also recorded in docs/limits.md: RLS here keys on the
`app.tenant_id` session variable, so it defends against a middleware bug —
the API forgetting to scope a query — not against an attacker who already
controls the SQL session and can set the variable themselves. The boundary
against that adversary is API-key-to-tenant resolution (Phase 04.2), and
pretending otherwise would be the kind of overclaim this project avoids.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from db.seed import CLINIC, FINANCE, OPS, fake_embedding

pytestmark = pytest.mark.security

OTHER_TENANTS = [OPS, FINANCE]

TENANT_SCOPED_TABLES = [
    "mnemos.episodic_events",
    "mnemos.semantic_facts",
    "mnemos.fact_provenance",
    "mnemos.audit_chain",
    "mnemos.legal_holds",
]


@pytest.fixture
def api_conn(role_conn):
    """An API-role session scoped to the clinic tenant, as the middleware would."""
    conn = role_conn("mnemos_api")
    with conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = '{CLINIC}'")
    return conn


def _require_seed(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mnemos.semantic_facts")
        if cur.fetchone()[0] == 0:
            pytest.skip("no seeded data. Run: make db-seed")


@pytest.mark.parametrize("table", TENANT_SCOPED_TABLES)
def test_cannot_read_other_tenants_rows(table: str, api_conn) -> None:
    _require_seed(api_conn)
    with api_conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE tenant_id = ANY(%s)", (OTHER_TENANTS,))
        assert cur.fetchone()[0] == 0


@pytest.mark.parametrize("table", TENANT_SCOPED_TABLES)
def test_unscoped_select_returns_only_own_tenant(table: str, api_conn) -> None:
    """The likelier real bug: a query that simply forgot its WHERE clause."""
    _require_seed(api_conn)
    with api_conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT tenant_id FROM {table}")
        seen = {row[0] for row in cur.fetchall()}
    assert seen <= {CLINIC}, f"{table} leaked tenants: {seen - {CLINIC}}"


OPS_RUNBOOK_TEXT = (
    "Checkout latency spikes have twice been caused by an unindexed foreign key "
    "on a hot read path. Check for missing indexes before scaling."
)


def test_vector_index_partitions_by_tenant_prefix(admin_conn) -> None:
    """Prove the isolation lives INSIDE the index, not after it.

    The behavioural test below passes even with a badly-scoped index, because RLS
    would filter the leaked neighbours before we saw them. That is a real defense
    but a weak proof, so this asserts on the query plan directly: the scan must
    be a `vector search` against ix_facts_embedding with `prefix spans` pinned to
    one tenant. Filter-after-ANN would show an unbounded vector search followed
    by a filter, and would silently degrade recall as tenants grow — a
    correctness problem wearing a performance costume.
    """
    with admin_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mnemos.semantic_facts")
        if cur.fetchone()[0] == 0:
            pytest.skip("no seeded data. Run: make db-seed")
        cur.execute(
            "EXPLAIN SELECT fact_id FROM mnemos.semantic_facts WHERE tenant_id = %s "
            "ORDER BY embedding <-> %s::VECTOR LIMIT 5",
            (CLINIC, fake_embedding(OPS_RUNBOOK_TEXT)),
        )
        plan = "\n".join(row[0] for row in cur.fetchall())

    assert "vector search" in plan, f"ANN index not used:\n{plan}"
    assert "ix_facts_embedding" in plan, f"wrong index:\n{plan}"
    assert "prefix spans" in plan, f"index not partitioned by tenant:\n{plan}"
    assert str(CLINIC) in plan, f"prefix span not pinned to the tenant:\n{plan}"


def test_vector_search_does_not_cross_tenants(api_conn) -> None:
    """Query with an embedding of another tenant's fact and get nothing back.

    The text is taken verbatim from the ops tenant's seeded runbook, so its
    embedding is the nearest possible neighbour to a row this session must not
    see.
    """
    _require_seed(api_conn)
    with api_conn.cursor() as cur:
        cur.execute(
            """
            SELECT tenant_id, subject_key
            FROM mnemos.semantic_facts
            ORDER BY embedding <-> %s::VECTOR
            LIMIT 20
            """,
            (fake_embedding(OPS_RUNBOOK_TEXT),),
        )
        rows = cur.fetchall()
    assert all(row[0] == CLINIC for row in rows), "vector search crossed a tenant boundary"


def test_full_text_search_does_not_cross_tenants(api_conn) -> None:
    _require_seed(api_conn)
    with api_conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id FROM mnemos.semantic_facts "
            "WHERE tsv @@ plainto_tsquery('latency indexes scaling')"
        )
        assert all(row[0] == CLINIC for row in cur.fetchall())


def test_cte_cannot_smuggle_other_tenant_rows(api_conn) -> None:
    """Crafted SQL: hide the cross-tenant read inside a CTE."""
    _require_seed(api_conn)
    with api_conn.cursor() as cur:
        cur.execute(
            """
            WITH everything AS (SELECT tenant_id, subject_key FROM mnemos.semantic_facts)
            SELECT count(*) FROM everything WHERE tenant_id = ANY(%s)
            """,
            (OTHER_TENANTS,),
        )
        assert cur.fetchone()[0] == 0


def test_subquery_cannot_smuggle_other_tenant_rows(api_conn) -> None:
    _require_seed(api_conn)
    with api_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM mnemos.episodic_events WHERE subject_key IN "
            "(SELECT subject_key FROM mnemos.semantic_facts WHERE tenant_id = ANY(%s))",
            (OTHER_TENANTS,),
        )
        assert cur.fetchone()[0] == 0


def test_join_cannot_smuggle_other_tenant_rows(api_conn) -> None:
    _require_seed(api_conn)
    with api_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM mnemos.semantic_facts f "
            "JOIN mnemos.fact_provenance p ON p.fact_id = f.fact_id "
            "WHERE f.tenant_id = ANY(%s) OR p.tenant_id = ANY(%s)",
            (OTHER_TENANTS, OTHER_TENANTS),
        )
        assert cur.fetchone()[0] == 0


def test_cannot_write_into_another_tenant(api_conn) -> None:
    """WITH CHECK, not just USING.

    A policy with only USING lets a session read its own rows while writing rows
    labelled as someone else's — which is worse than a read leak, because it
    plants data rather than exposing it.
    """
    _require_seed(api_conn)
    api_conn.autocommit = False
    try:
        with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.RaiseException)):
            with api_conn.transaction(), api_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO mnemos.audit_chain "
                    "(tenant_id, shard_id, seq, op, actor, payload, payload_hash, "
                    " prev_hash, entry_hash) "
                    "VALUES (%s, 0, 999999, 'remember', 'attacker', '{}'::JSONB, "
                    "'\\x00', '\\x00', '\\x00')",
                    (OPS,),
                )
    finally:
        api_conn.autocommit = True


def test_cannot_update_another_tenants_facts(api_conn) -> None:
    _require_seed(api_conn)
    with api_conn.cursor() as cur:
        cur.execute(
            "UPDATE mnemos.semantic_facts SET strength = 99 WHERE tenant_id = ANY(%s)",
            (OTHER_TENANTS,),
        )
        assert cur.rowcount == 0


def test_returning_clause_leaks_nothing(api_conn) -> None:
    """UPDATE ... RETURNING is a classic oracle: the filter blocks the write but
    the RETURNING clause hands back the rows anyway."""
    _require_seed(api_conn)
    with api_conn.cursor() as cur:
        cur.execute(
            "UPDATE mnemos.semantic_facts SET strength = strength "
            "WHERE tenant_id = ANY(%s) RETURNING tenant_id, subject_key",
            (OTHER_TENANTS,),
        )
        assert cur.fetchall() == []


def test_unset_tenant_context_sees_nothing(role_conn) -> None:
    """Fail closed.

    If the middleware never sets app.tenant_id, current_tenant() is NULL and the
    policy must match no rows. Failing *open* here would turn one forgotten line
    of middleware into a full cross-tenant read.
    """
    conn = role_conn("mnemos_api")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mnemos.semantic_facts")
        assert cur.fetchone()[0] == 0


def test_garbage_tenant_context_sees_nothing(role_conn) -> None:
    conn = role_conn("mnemos_api")
    with conn.cursor() as cur:
        cur.execute(f"SET app.tenant_id = '{uuid.uuid4()}'")
        cur.execute("SELECT count(*) FROM mnemos.semantic_facts")
        assert cur.fetchone()[0] == 0
