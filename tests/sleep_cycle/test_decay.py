"""Weekly strength decay: the curve itself, then the sweep against a DB."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
from mnemos_engine.crypto import Envelope
from mnemos_engine.db import Database
from mnemos_engine.embeddings import Embedder
from mnemos_engine.ledger import append_audit
from mnemos_engine.models import Op
from mnemos_sleep_cycle import decay

from .conftest import seed_fact

# --------------------------------------------------------------- pure curve


def test_decayed_strength_unchanged_when_not_idle() -> None:
    assert decay.decayed_strength(1.0, 0.0) == 1.0
    assert decay.decayed_strength(1.0, -1.0) == 1.0


def test_decayed_strength_decreases_monotonically_with_idle_time() -> None:
    one_week = decay.decayed_strength(1.0, 1.0)
    ten_weeks = decay.decayed_strength(1.0, 10.0)
    fifty_weeks = decay.decayed_strength(1.0, 50.0)
    assert 1.0 > one_week > ten_weeks >= fifty_weeks


def test_decayed_strength_never_breaches_the_floor() -> None:
    assert decay.decayed_strength(1.0, 10_000.0) == decay.MIN_STRENGTH
    assert decay.decayed_strength(decay.MIN_STRENGTH, 10.0) == decay.MIN_STRENGTH


def test_decayed_strength_higher_lambda_decays_faster() -> None:
    gentle = decay.decayed_strength(1.0, 5.0, lam=0.05)
    aggressive = decay.decayed_strength(1.0, 5.0, lam=0.5)
    assert gentle > aggressive


# ------------------------------------------------------------- against a DB


async def test_decay_tenant_facts_decays_idle_facts_only(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    idle_id = await seed_fact(
        db,
        tenant,
        subject_key="patient:us:idle",
        text="an idle claim",
        embedder=embedder,
        envelope=envelope,
        strength=1.0,
    )
    recent_id = await seed_fact(
        db,
        tenant,
        subject_key="patient:us:recent",
        text="a recently recalled claim",
        embedder=embedder,
        envelope=envelope,
        strength=1.0,
    )

    async def backdate(cur: psycopg.AsyncCursor) -> None:
        await append_audit(
            cur,
            tenant,
            op=Op.CONSOLIDATE,
            actor="test",
            subject_key=None,
            payload={},
        )
        await cur.execute(
            "UPDATE mnemos.semantic_facts SET created_at = %s, last_recalled_at = NULL "
            "WHERE tenant_id = %s AND fact_id = %s",
            (datetime.now(UTC) - timedelta(days=90), tenant, idle_id),
        )
        await cur.execute(
            "UPDATE mnemos.semantic_facts SET last_recalled_at = %s "
            "WHERE tenant_id = %s AND fact_id = %s",
            (datetime.now(UTC) - timedelta(days=1), tenant, recent_id),
        )

    await db.transaction(tenant, backdate, label="backdate")

    async def sweep(cur: psycopg.AsyncCursor) -> int:
        return await decay.decay_tenant_facts(cur, tenant, actor="test")

    changed = await db.transaction(tenant, sweep, label="decay_sweep")
    assert changed == 1

    async def read_strength(cur: psycopg.AsyncCursor, fact_id: uuid.UUID) -> float:
        await cur.execute(
            "SELECT strength FROM mnemos.semantic_facts WHERE tenant_id = %s AND fact_id = %s",
            (tenant, fact_id),
        )
        row = await cur.fetchone()
        return float(row[0])

    idle_strength = await db.transaction(
        tenant, lambda cur: read_strength(cur, idle_id), label="read", read_only=True
    )
    recent_strength = await db.transaction(
        tenant, lambda cur: read_strength(cur, recent_id), label="read", read_only=True
    )
    assert idle_strength < 1.0
    assert recent_strength == 1.0


async def test_decay_tenant_facts_is_a_true_no_op_when_nothing_is_idle(
    db: Database, tenant: uuid.UUID, embedder: Embedder, envelope: Envelope
) -> None:
    await seed_fact(
        db,
        tenant,
        subject_key="patient:us:brand-new",
        text="just written",
        embedder=embedder,
        envelope=envelope,
    )

    async def sweep(cur: psycopg.AsyncCursor) -> int:
        return await decay.decay_tenant_facts(cur, tenant, actor="test")

    changed = await db.transaction(tenant, sweep, label="decay_sweep")
    assert changed == 0
