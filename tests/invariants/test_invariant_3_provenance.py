"""INVARIANT 3 — No fact becomes recallable without provenance to at least one
episode.

Protects: an agent must never act on a belief that cannot be traced to something
observed. Without this, the sleep cycle could hallucinate a fact, nothing would
link it to a source, and no deposition could ever explain where it came from.

Bound at *promotion* rather than insert: consolidation legitimately creates a
fact and its provenance edges inside one transaction, so enforcing at insert
would forbid the correct write order. Promotion is where the property matters,
because 'unverified' facts are excluded from recall anyway.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.conftest import append_audit, insert_episode, insert_fact

pytestmark = pytest.mark.invariant

RECALLABLE_STATES = ["corroborated", "trusted"]


@pytest.mark.parametrize("trust", RECALLABLE_STATES)
def test_cannot_insert_orphan_fact_as_recallable(trust: str, txn_conn, tenant: uuid.UUID) -> None:
    with pytest.raises(psycopg.errors.RaiseException, match="invariant 3"):
        with txn_conn.transaction(), txn_conn.cursor() as cur:
            append_audit(cur, tenant)
            insert_fact(cur, tenant, "subject:orphan", trust=trust)


@pytest.mark.parametrize("trust", RECALLABLE_STATES)
def test_cannot_promote_orphan_fact(trust: str, txn_conn, tenant: uuid.UUID) -> None:
    """The likelier real-world path: the fact already exists, and something
    tries to promote it without ever attaching evidence."""
    with txn_conn.transaction(), txn_conn.cursor() as cur:
        append_audit(cur, tenant)
        fact_id = insert_fact(cur, tenant, "subject:promote-orphan")

    with pytest.raises(psycopg.errors.RaiseException, match="invariant 3"):
        with txn_conn.transaction(), txn_conn.cursor() as cur:
            append_audit(cur, tenant, op="promote")
            cur.execute(
                "UPDATE mnemos.semantic_facts SET trust = %s WHERE tenant_id = %s AND fact_id = %s",
                (trust, tenant, fact_id),
            )


def test_unverified_facts_may_exist_without_provenance(txn_conn, tenant: uuid.UUID) -> None:
    """Deliberately permitted.

    A fact mid-consolidation has no edges yet. Forbidding that state would make
    the legitimate write order impossible — and 'unverified' facts never reach
    recall, so nothing can act on them.
    """
    with txn_conn.transaction(), txn_conn.cursor() as cur:
        append_audit(cur, tenant)
        fact_id = insert_fact(cur, tenant, "subject:pending", trust="unverified")

    with txn_conn.transaction(), txn_conn.cursor() as cur:
        cur.execute(
            "SELECT trust FROM mnemos.semantic_facts WHERE tenant_id = %s AND fact_id = %s",
            (tenant, fact_id),
        )
        assert cur.fetchone()[0] == "unverified"


def test_promotion_succeeds_once_provenance_exists(txn_conn, tenant: uuid.UUID) -> None:
    with txn_conn.transaction(), txn_conn.cursor() as cur:
        append_audit(cur, tenant)
        event_id = insert_episode(cur, tenant, "subject:proper")
        fact_id = insert_fact(cur, tenant, "subject:proper")
        cur.execute(
            "INSERT INTO mnemos.fact_provenance (tenant_id, fact_id, event_id, subject_key) "
            "VALUES (%s, %s, %s, 'subject:proper')",
            (tenant, fact_id, event_id),
        )

    with txn_conn.transaction(), txn_conn.cursor() as cur:
        append_audit(cur, tenant, op="promote")
        cur.execute(
            "UPDATE mnemos.semantic_facts SET trust = 'trusted' "
            "WHERE tenant_id = %s AND fact_id = %s",
            (tenant, fact_id),
        )
        assert cur.rowcount == 1
