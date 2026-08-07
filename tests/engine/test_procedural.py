"""Procedural memory — the tier where a wrong answer becomes a wrong action."""

from __future__ import annotations

import uuid

import pytest
from mnemos_engine.crypto import Envelope, LocalKeyWrapper
from mnemos_engine.db import Database
from mnemos_engine.embeddings import FakeEmbedder
from mnemos_engine.models import SourceTrust, Trust
from mnemos_engine.procedural import (
    DEMOTION_FAILURE_THRESHOLD,
    find_skill,
    learn_skill,
    record_outcome,
)

LOCAL_DSN = "postgresql://root@localhost:26257/mnemos?sslmode=disable"

PLAYBOOK = (
    "1. Check for missing indexes on the hot read path.\n"
    "2. Add the index.\n"
    "3. Confirm p99 recovers within five minutes."
)
TASK = "Checkout latency spike during peak traffic"


@pytest.fixture
async def db() -> Database:
    database = Database(LOCAL_DSN, min_size=1, max_size=4)
    try:
        await database.open()
    except Exception as exc:
        pytest.skip(f"local CockroachDB unavailable ({exc}). Run: make db-local")
    yield database
    await database.close()


@pytest.fixture
async def tenant(db: Database) -> uuid.UUID:
    tenant_id = uuid.uuid4()

    async def create(cur):
        await cur.execute(
            "INSERT INTO mnemos.tenants (tenant_id, slug, display_name) VALUES (%s, %s, %s)",
            (tenant_id, f"proc-{tenant_id.hex[:8]}", "Procedural test"),
        )

    await db.transaction(None, create, label="create_tenant")
    return tenant_id


def _kit() -> dict:
    return {"embedder": FakeEmbedder(), "envelope": Envelope(LocalKeyWrapper())}


async def _learn(db: Database, tenant: uuid.UUID, source_trust: SourceTrust, name: str):
    async def run(cur):
        return await learn_skill(
            cur,
            tenant,
            name=name,
            playbook=PLAYBOOK,
            task_description=TASK,
            source_trust=source_trust,
            actor="test",
            **_kit(),
        )

    return await db.transaction(tenant, run, label="learn")


async def test_operator_authored_skill_is_trusted_on_arrival(
    db: Database, tenant: uuid.UUID
) -> None:
    skill = await _learn(db, tenant, SourceTrust.OPERATOR, "index-check")
    assert skill.trust is Trust.TRUSTED
    assert skill.is_executable


@pytest.mark.parametrize("source", [SourceTrust.AGENT, SourceTrust.EXTERNAL])
async def test_agent_authored_skill_is_quarantined(
    source: SourceTrust, db: Database, tenant: uuid.UUID
) -> None:
    """The strictest gate in the system.

    An agent that can write its own procedure and then execute it closes the
    loop from "read attacker-controlled text" to "perform attacker-chosen
    action" with nothing in between.
    """
    skill = await _learn(db, tenant, source, f"self-taught-{source}")
    assert skill.trust is Trust.QUARANTINED
    assert not skill.is_executable


async def test_find_skill_matches_a_paraphrased_task(db: Database, tenant: uuid.UUID) -> None:
    await _learn(db, tenant, SourceTrust.OPERATOR, "index-check")

    async def run(cur):
        return await find_skill(cur, tenant, TASK, **_kit())

    found = await db.transaction(tenant, run, label="find")
    assert [s.name for s in found] == ["index-check"]
    assert found[0].playbook == PLAYBOOK


async def test_find_skill_will_not_return_a_quarantined_playbook(
    db: Database, tenant: uuid.UUID
) -> None:
    """The gate has to hold at the read side too.

    Quarantining on write and then serving it anyway would be a defense that
    exists only in the schema.
    """
    await _learn(db, tenant, SourceTrust.EXTERNAL, "attacker-runbook")

    async def gated(cur):
        return await find_skill(cur, tenant, TASK, **_kit())

    assert await db.transaction(tenant, gated, label="find") == []

    async def reviewed(cur):
        return await find_skill(cur, tenant, TASK, include_quarantined=True, **_kit())

    visible = await db.transaction(tenant, reviewed, label="find")
    assert [s.name for s in visible] == ["attacker-runbook"]
    assert not visible[0].is_executable, "visible for human review is not the same as executable"


async def test_learning_again_creates_a_new_version(db: Database, tenant: uuid.UUID) -> None:
    """Versions are never overwritten.

    A superseded runbook is exactly what a deposition needs when explaining why
    an agent did something last month.
    """
    first = await _learn(db, tenant, SourceTrust.OPERATOR, "index-check")
    second = await _learn(db, tenant, SourceTrust.OPERATOR, "index-check")
    assert first.skill_id == second.skill_id
    assert (first.version, second.version) == (1, 2)


async def test_repeated_failure_demotes_a_trusted_skill(db: Database, tenant: uuid.UUID) -> None:
    """A playbook that keeps not working should stop being offered.

    Nobody has to notice; the fitness counters do it.
    """
    skill = await _learn(db, tenant, SourceTrust.OPERATOR, "flaky-runbook")

    trust = skill.trust
    for _ in range(DEMOTION_FAILURE_THRESHOLD):

        async def run(cur):
            return await record_outcome(
                cur,
                tenant,
                skill_id=skill.skill_id,
                version=skill.version,
                success=False,
                actor="test",
            )

        trust = await db.transaction(tenant, run, label="outcome")

    assert trust is Trust.QUARANTINED

    async def gated(cur):
        return await find_skill(cur, tenant, TASK, **_kit())

    assert await db.transaction(tenant, gated, label="find") == []


async def test_scattered_failures_do_not_demote(db: Database, tenant: uuid.UUID) -> None:
    """Consecutive failures, not lifetime ratio.

    A runbook with many successes and a few scattered failures is fine; one that
    has failed the last three times is not, even if its overall record looks
    healthy. Demoting on ratio would punish well-used playbooks for being used.
    """
    skill = await _learn(db, tenant, SourceTrust.OPERATOR, "mostly-good")

    trust = skill.trust
    for success in [False, True, False, True, False]:

        async def run(cur, success=success):
            return await record_outcome(
                cur,
                tenant,
                skill_id=skill.skill_id,
                version=skill.version,
                success=success,
                actor="test",
            )

        trust = await db.transaction(tenant, run, label="outcome")

    assert trust is Trust.TRUSTED


async def test_fitness_is_reported(db: Database, tenant: uuid.UUID) -> None:
    skill = await _learn(db, tenant, SourceTrust.OPERATOR, "measured")

    for success in [True, True, False]:

        async def run(cur, success=success):
            return await record_outcome(
                cur,
                tenant,
                skill_id=skill.skill_id,
                version=skill.version,
                success=success,
                actor="test",
            )

        await db.transaction(tenant, run, label="outcome")

    async def find(cur):
        return await find_skill(cur, tenant, TASK, **_kit())

    found = await db.transaction(tenant, find, label="find")
    match = next(s for s in found if s.name == "measured")
    assert (match.successes, match.failures) == (2, 1)
    assert match.fitness == pytest.approx(2 / 3)
