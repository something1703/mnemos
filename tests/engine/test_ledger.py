"""The audit ledger against a live cluster.

Covers the two adversaries the design distinguishes:

  * someone who edits or removes a row — caught by per-entry hashes;
  * someone who rewrites an entire shard and recomputes its internal hashes —
    invisible to per-entry checks, caught only by a Merkle root that was already
    committed.

The second case is why checkpoints exist, and the test for it is the one that
proves they earn their keep.
"""

from __future__ import annotations

import uuid

import pytest
from mnemos_engine.canonical import GENESIS_HASH, payload_hash
from mnemos_engine.db import Database
from mnemos_engine.ledger import append_audit, checkpoint, verify_chain
from mnemos_engine.models import Op

pytestmark = pytest.mark.invariant

LOCAL_DSN = "postgresql://root@localhost:26257/mnemos?sslmode=disable"


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
async def ledger_tenant(db: Database) -> uuid.UUID:
    tenant_id = uuid.uuid4()

    async def create(cur):
        await cur.execute(
            "INSERT INTO mnemos.tenants (tenant_id, slug, display_name) VALUES (%s, %s, %s)",
            (tenant_id, f"led-{tenant_id.hex[:8]}", "Ledger test"),
        )

    await db.transaction(None, create, label="create_tenant")
    return tenant_id


async def _append(db: Database, tenant_id: uuid.UUID, subject: str, op: Op = Op.REMEMBER) -> None:
    async def run(cur):
        await append_audit(cur, tenant_id, op=op, actor="test", subject_key=subject)

    await db.transaction(tenant_id, run, label="append")


async def test_first_entry_chains_from_genesis(db: Database, ledger_tenant: uuid.UUID) -> None:
    await _append(db, ledger_tenant, "subject:a")

    async def read(cur):
        await cur.execute(
            "SELECT seq, prev_hash FROM mnemos.audit_chain WHERE tenant_id = %s",
            (ledger_tenant,),
        )
        return await cur.fetchall()

    rows = await db.transaction(ledger_tenant, read, label="read")
    assert len(rows) == 1
    assert int(rows[0][0]) == 1
    assert bytes(rows[0][1]) == GENESIS_HASH


async def test_entries_for_one_subject_form_one_ordered_chain(
    db: Database, ledger_tenant: uuid.UUID
) -> None:
    for _ in range(5):
        await _append(db, ledger_tenant, "patient:eu:8f2c")

    async def read(cur):
        await cur.execute(
            "SELECT DISTINCT shard_id FROM mnemos.audit_chain WHERE tenant_id = %s",
            (ledger_tenant,),
        )
        shards = await cur.fetchall()
        await cur.execute(
            "SELECT seq FROM mnemos.audit_chain WHERE tenant_id = %s ORDER BY seq",
            (ledger_tenant,),
        )
        return shards, await cur.fetchall()

    shards, seqs = await db.transaction(ledger_tenant, read, label="read")
    assert len(shards) == 1, "a subject's history must not fragment across shards"
    assert [int(s[0]) for s in seqs] == [1, 2, 3, 4, 5]


async def test_different_subjects_spread_across_shards(
    db: Database, ledger_tenant: uuid.UUID
) -> None:
    """The throughput property. One chain per tenant would serialise every write."""
    for i in range(40):
        await _append(db, ledger_tenant, f"subject:{i}")

    async def read(cur):
        await cur.execute(
            "SELECT count(DISTINCT shard_id) FROM mnemos.audit_chain WHERE tenant_id = %s",
            (ledger_tenant,),
        )
        return (await cur.fetchone())[0]

    assert int(await db.transaction(ledger_tenant, read, label="read")) > 1


async def test_clean_chain_verifies(db: Database, ledger_tenant: uuid.UUID) -> None:
    for i in range(12):
        await _append(db, ledger_tenant, f"subject:{i % 3}")

    async def run(cur):
        return await verify_chain(cur, ledger_tenant)

    result = await db.transaction(ledger_tenant, run, label="verify")
    assert result.valid
    assert result.entries_checked == 12


async def test_edited_payload_is_caught(db: Database, ledger_tenant: uuid.UUID) -> None:
    """The single-bit tamper. Rewriting history must not be free."""
    await _append(db, ledger_tenant, "subject:tamper")

    async def tamper(cur):
        await cur.execute(
            "UPDATE mnemos.audit_chain SET payload = payload || '{\"injected\": true}'::JSONB "
            "WHERE tenant_id = %s",
            (ledger_tenant,),
        )

    await db.transaction(ledger_tenant, tamper, label="tamper")

    async def run(cur):
        return await verify_chain(cur, ledger_tenant)

    result = await db.transaction(ledger_tenant, run, label="verify")
    assert not result.valid
    assert "recorded hash" in (result.detail or "")


async def test_deleted_row_is_caught_by_the_sequence(
    db: Database, ledger_tenant: uuid.UUID
) -> None:
    """Removing an entry leaves the survivors chaining to each other correctly;
    only the sequence reveals the hole."""
    for _ in range(4):
        await _append(db, ledger_tenant, "subject:gap")

    async def delete_middle(cur):
        await cur.execute(
            "DELETE FROM mnemos.audit_chain WHERE tenant_id = %s AND seq = 2", (ledger_tenant,)
        )

    await db.transaction(ledger_tenant, delete_middle, label="delete")

    async def run(cur):
        return await verify_chain(cur, ledger_tenant)

    result = await db.transaction(ledger_tenant, run, label="verify")
    assert not result.valid
    assert "sequence gap" in (result.detail or "")


async def test_internally_consistent_shard_rewrite_is_caught_by_the_checkpoint(
    db: Database, ledger_tenant: uuid.UUID
) -> None:
    """The forgery per-entry hashes cannot see.

    An attacker who controls the database can rewrite a shard AND recompute every
    hash inside it, producing a chain that verifies perfectly on its own terms.
    Only a root committed before the rewrite reveals it — which is the argument
    for checkpoints, and (in Phase 06.6) for anchoring them outside the database.
    """
    for _ in range(3):
        await _append(db, ledger_tenant, "subject:rewrite")

    async def make_checkpoint(cur):
        return await checkpoint(cur, ledger_tenant)

    committed = await db.transaction(ledger_tenant, make_checkpoint, label="checkpoint")
    assert committed.entry_count > 0

    async def rewrite(cur):
        # Rewrite the entire shard consistently: new payload, recomputed
        # payload_hash and entry_hash, correct prev links, correct head.
        await cur.execute(
            "SELECT shard_id, seq FROM mnemos.audit_chain "
            "WHERE tenant_id = %s AND subject_key = 'subject:rewrite' ORDER BY seq",
            (ledger_tenant,),
        )
        rows = await cur.fetchall()
        prev = GENESIS_HASH
        for shard_id, seq in rows:
            body = {"op": "remember", "actor": "attacker", "seq": int(seq), "forged": True}
            digest = payload_hash(body)
            from mnemos_engine.canonical import entry_hash as eh

            entry = eh(digest, prev)
            await cur.execute(
                "UPDATE mnemos.audit_chain SET payload = %s, payload_hash = %s, "
                "prev_hash = %s, entry_hash = %s "
                "WHERE tenant_id = %s AND shard_id = %s AND seq = %s",
                (
                    __import__("json").dumps(body),
                    digest,
                    prev,
                    entry,
                    ledger_tenant,
                    shard_id,
                    seq,
                ),
            )
            prev = entry
        await cur.execute(
            "UPDATE mnemos.chain_heads SET entry_hash = %s WHERE tenant_id = %s AND shard_id = %s",
            (prev, ledger_tenant, rows[0][0]),
        )

    await db.transaction(ledger_tenant, rewrite, label="rewrite")

    async def run(cur):
        return await verify_chain(cur, ledger_tenant)

    result = await db.transaction(ledger_tenant, run, label="verify")
    assert not result.valid, "an internally consistent shard rewrite went undetected"
    assert "committed Merkle root" in (result.detail or ""), (
        f"expected the checkpoint to catch it, got: {result.detail}"
    )


async def test_checkpoint_binds_every_shard(db: Database, ledger_tenant: uuid.UUID) -> None:
    for i in range(20):
        await _append(db, ledger_tenant, f"subject:{i}")

    async def run(cur):
        made = await checkpoint(cur, ledger_tenant)
        await cur.execute(
            "SELECT count(*) FROM mnemos.chain_heads WHERE tenant_id = %s", (ledger_tenant,)
        )
        head_count = int((await cur.fetchone())[0])
        return made, head_count

    made, head_count = await db.transaction(ledger_tenant, run, label="checkpoint")
    assert len(made.shard_heads) == head_count
    assert not made.is_anchored, "a fresh checkpoint is not evidence until it is anchored"
