"""INVARIANT 2 — Every state-changing memory op appends a hash-chained audit row
in the same transaction.

Protects: the audit trail cannot silently disagree with reality. If a mutation
could commit without its audit row, the record would be a lie in the direction
that matters most — the one where something happened and nothing recorded it.

Enforced by a database trigger (migration 010), so these tests also serve as the
regression suite for a CockroachDB upgrade that changes trigger or session-
variable behaviour.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.conftest import append_audit, insert_episode, insert_fact

pytestmark = pytest.mark.invariant

PROTECTED_TABLES = ["mnemos.episodic_events", "mnemos.semantic_facts"]


def test_write_without_audit_ticket_is_rejected(txn_conn, tenant: uuid.UUID) -> None:
    with pytest.raises(psycopg.errors.RaiseException, match="invariant 2"):
        with txn_conn.transaction(), txn_conn.cursor() as cur:
            insert_episode(cur, tenant, "subject:no-ticket")


def test_forged_ticket_is_rejected(txn_conn, tenant: uuid.UUID) -> None:
    """A ticket that resolves to no audit row buys nothing.

    Without this check the trigger would be satisfied by any well-formed UUID,
    which is to say by nothing at all.
    """
    with pytest.raises(psycopg.errors.RaiseException, match="has no audit row"):
        with txn_conn.transaction(), txn_conn.cursor() as cur:
            cur.execute(f"SET LOCAL app.audit_ticket = '{uuid.uuid4()}'")
            insert_episode(cur, tenant, "subject:forged")


def test_ticket_does_not_leak_across_transactions(txn_conn, tenant: uuid.UUID) -> None:
    """SET LOCAL must be transaction-scoped.

    If a ticket survived its transaction, one audited write would license an
    unbounded number of unaudited ones on the same connection — which is the
    most likely way this design would quietly fail.
    """
    with txn_conn.transaction(), txn_conn.cursor() as cur:
        append_audit(cur, tenant)
        insert_episode(cur, tenant, "subject:legit")

    with pytest.raises(psycopg.errors.RaiseException, match="without an audit ticket"):
        with txn_conn.transaction(), txn_conn.cursor() as cur:
            insert_episode(cur, tenant, "subject:reused-ticket")


def test_audited_write_succeeds(txn_conn, tenant: uuid.UUID) -> None:
    with txn_conn.transaction(), txn_conn.cursor() as cur:
        append_audit(cur, tenant)
        event_id = insert_episode(cur, tenant, "subject:audited")

    with txn_conn.transaction(), txn_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM mnemos.episodic_events WHERE tenant_id = %s AND event_id = %s",
            (tenant, event_id),
        )
        assert cur.fetchone()[0] == 1


def test_rollback_leaves_neither_the_row_nor_the_audit(txn_conn, tenant: uuid.UUID) -> None:
    """Atomicity in the other direction.

    The audit row and the mutation share a transaction, so a failure after the
    audit append must discard both. An orphan audit row claiming a write that
    never happened is its own kind of lie.
    """
    ticket: uuid.UUID | None = None
    with pytest.raises(RuntimeError):
        with txn_conn.transaction(), txn_conn.cursor() as cur:
            ticket = append_audit(cur, tenant, subject_key="subject:rollback")
            insert_episode(cur, tenant, "subject:rollback")
            raise RuntimeError("injected fault after the audit append")

    with txn_conn.transaction(), txn_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM mnemos.audit_chain WHERE ticket = %s", (ticket,))
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM mnemos.episodic_events "
            "WHERE tenant_id = %s AND subject_key = 'subject:rollback'",
            (tenant,),
        )
        assert cur.fetchone()[0] == 0


@pytest.mark.parametrize("table", PROTECTED_TABLES)
def test_updates_are_audited_too(table: str, txn_conn, tenant: uuid.UUID) -> None:
    """Not just inserts.

    Reinforcement, decay, promotion, and supersession are all UPDATEs, and each
    changes what the agent will believe. An unaudited UPDATE would be a silent
    edit to memory.
    """
    with txn_conn.transaction(), txn_conn.cursor() as cur:
        append_audit(cur, tenant)
        if table == "mnemos.episodic_events":
            insert_episode(cur, tenant, "subject:update")
        else:
            insert_fact(cur, tenant, "subject:update")

    with pytest.raises(psycopg.errors.RaiseException, match="invariant 2"):
        with txn_conn.transaction(), txn_conn.cursor() as cur:
            if table == "mnemos.episodic_events":
                cur.execute(
                    "UPDATE mnemos.episodic_events SET event_type = 'tampered' "
                    "WHERE tenant_id = %s AND subject_key = 'subject:update'",
                    (tenant,),
                )
            else:
                cur.execute(
                    "UPDATE mnemos.semantic_facts SET strength = 99 "
                    "WHERE tenant_id = %s AND subject_key = 'subject:update'",
                    (tenant,),
                )
