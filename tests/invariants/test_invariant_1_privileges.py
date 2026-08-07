"""INVARIANT 1 — No LLM-driven process ever holds DELETE or governance privileges.

Protects: an agent whose input can be manipulated must not be able to destroy
memory. Prompt injection should be able to make an agent wrong; it must not be
able to make data disappear.

These tests connect *as* each service role and attempt to delete. Reading the
grant tables and trusting our own interpretation of them would prove nothing —
the question is what the database actually does when the API tries.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.conftest import append_audit, insert_episode

pytestmark = pytest.mark.invariant

# Every role that runs alongside a language model. None may destroy.
LLM_ADJACENT_ROLES = ["mnemos_api", "mnemos_pipeline", "mnemos_readonly"]

MEMORY_TABLES = [
    "mnemos.episodic_events",
    "mnemos.semantic_facts",
    "mnemos.fact_provenance",
    "mnemos.audit_chain",
]


@pytest.mark.parametrize("role", LLM_ADJACENT_ROLES)
@pytest.mark.parametrize("table", MEMORY_TABLES)
def test_llm_adjacent_roles_cannot_delete(role: str, table: str, role_conn) -> None:
    conn = role_conn(role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (uuid.uuid4(),))


@pytest.mark.parametrize("role", LLM_ADJACENT_ROLES)
def test_llm_adjacent_roles_cannot_drop_the_audit_trail(role: str, role_conn) -> None:
    """The subtler attack: don't delete rows, remove the table that records them."""
    conn = role_conn(role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.cursor() as cur:
        cur.execute("DROP TABLE mnemos.audit_chain")


@pytest.mark.parametrize("role", LLM_ADJACENT_ROLES)
def test_llm_adjacent_roles_cannot_disable_the_invariant_trigger(role: str, role_conn) -> None:
    """Nor remove the enforcement itself.

    A principal that can drop the trigger can then write unaudited rows, so this
    is the same privilege as deleting — and is denied the same way.
    """
    conn = role_conn(role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.cursor() as cur:
        cur.execute("DROP TRIGGER require_audit ON mnemos.episodic_events")


@pytest.mark.parametrize("role", LLM_ADJACENT_ROLES)
def test_llm_adjacent_roles_cannot_touch_governance(role: str, role_conn) -> None:
    """Legal holds are a governance control, not memory. Only the Warden writes them.

    An agent that could release a hold could clear the way for an erasure it is
    otherwise forbidden from performing.
    """
    conn = role_conn(role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mnemos.legal_holds "
            "(tenant_id, subject_key, matter_reference, placed_by) "
            "VALUES (%s, 'x', 'forged', 'attacker')",
            (uuid.uuid4(),),
        )


def test_warden_can_delete(role_conn, tenant: uuid.UUID) -> None:
    """The other half of the invariant: destruction must remain possible.

    A system where nothing can be deleted fails the erasure requirement just as
    badly as one where anything can.
    """
    conn = role_conn("mnemos_warden", autocommit=False)
    with conn.transaction(), conn.cursor() as cur:
        append_audit(cur, tenant, op="remember")
        event_id = insert_episode(cur, tenant, "subject:warden-delete")

    with conn.transaction(), conn.cursor() as cur:
        append_audit(cur, tenant, op="forget")
        cur.execute(
            "DELETE FROM mnemos.episodic_events WHERE tenant_id = %s AND event_id = %s",
            (tenant, event_id),
        )
        assert cur.rowcount == 1
