"""The hash-chained audit ledger.

`append_audit` is called first inside every state-changing transaction. It
writes the chain entry and arms `app.audit_ticket`, which the database trigger
from migration 010 then requires on any protected mutation in the same
transaction. So invariant 2 holds in two independent ways: this code appends the
row, and the database refuses the mutation if it did not.

Chains are sharded by subject key. A subject's history stays one totally-ordered
chain — what an auditor reading a single record needs — while tenant throughput
scales with shard count instead of serialising on one row.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Json

from .canonical import GENESIS_HASH, entry_hash, merkle_root, payload_hash, shard_for
from .errors import ChainBroken
from .models import Checkpoint, Op, VerificationResult

log = logging.getLogger("mnemos.ledger")

DEFAULT_SHARDS = 16


async def append_audit(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    op: Op,
    actor: str,
    subject_key: str | None = None,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
    shard_count: int = DEFAULT_SHARDS,
) -> UUID:
    """Append one chain entry and arm its ticket for this transaction.

    Must be called inside a transaction opened by `Database.transaction`, before
    any protected mutation. Returns the ticket, which the caller rarely needs —
    the database consumes it via the session variable.
    """
    ticket = uuid4()
    shard = shard_for(subject_key or str(tenant_id), shard_count)

    # Lock the shard head. Two writers on the same shard serialise here rather
    # than producing a forked chain; writers on other shards are unaffected,
    # which is the entire reason for sharding.
    await cur.execute(
        "SELECT seq, entry_hash FROM mnemos.chain_heads "
        "WHERE tenant_id = %s AND shard_id = %s FOR UPDATE",
        (tenant_id, shard),
    )
    head = await cur.fetchone()

    if head is None:
        prev_seq, prev_hash = 0, GENESIS_HASH
    else:
        prev_seq, prev_hash = int(head[0]), bytes(head[1])

    seq = prev_seq + 1
    body: dict[str, Any] = {
        "op": str(op),
        "actor": actor,
        "subject_key": subject_key,
        "reason": reason,
        "seq": seq,
        "shard_id": shard,
        "tenant_id": str(tenant_id),
        "data": payload or {},
    }
    digest = payload_hash(body)
    entry = entry_hash(digest, prev_hash)

    await cur.execute(
        """
        INSERT INTO mnemos.audit_chain
            (tenant_id, shard_id, seq, ticket, op, subject_key, actor, reason,
             payload, payload_hash, prev_hash, entry_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            tenant_id,
            shard,
            seq,
            ticket,
            str(op),
            subject_key,
            actor,
            reason,
            Json(body),
            digest,
            prev_hash,
            entry,
        ),
    )

    if head is None:
        await cur.execute(
            "INSERT INTO mnemos.chain_heads (tenant_id, shard_id, seq, entry_hash) "
            "VALUES (%s, %s, %s, %s)",
            (tenant_id, shard, seq, entry),
        )
    else:
        await cur.execute(
            "UPDATE mnemos.chain_heads SET seq = %s, entry_hash = %s, updated_at = now() "
            "WHERE tenant_id = %s AND shard_id = %s",
            (seq, entry, tenant_id, shard),
        )

    # The trigger reads this. Interpolated rather than parameterised because
    # SET LOCAL does not accept bind parameters; the value is a UUID we just
    # generated, so no caller input reaches this string.
    await cur.execute(f"SET LOCAL app.audit_ticket = '{ticket}'")
    return ticket


async def checkpoint(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    actor: str = "system:checkpoint",
) -> Checkpoint:
    """Bind every shard head into one Merkle root.

    Sharding costs a single root of trust; this restores it. The root is what
    gets anchored to S3 Object Lock (Phase 06.6), and an internally-consistent
    rewrite of a whole shard is detectable only by comparing against an
    already-anchored root.
    """
    await cur.execute(
        "SELECT shard_id, seq, entry_hash FROM mnemos.chain_heads "
        "WHERE tenant_id = %s ORDER BY shard_id",
        (tenant_id,),
    )
    heads = await cur.fetchall()

    shard_heads = {
        str(int(row[0])): {"seq": int(row[1]), "hash": bytes(row[2]).hex()} for row in heads
    }
    root = merkle_root([bytes(row[2]) for row in heads])
    entry_count = sum(int(row[1]) for row in heads)

    await cur.execute(
        "SELECT COALESCE(MAX(checkpoint_seq), 0) + 1 FROM mnemos.chain_checkpoints "
        "WHERE tenant_id = %s",
        (tenant_id,),
    )
    row = await cur.fetchone()
    checkpoint_seq = int(row[0]) if row else 1
    covers_through = datetime.now(UTC)

    await append_audit(
        cur,
        tenant_id,
        op=Op.CHECKPOINT,
        actor=actor,
        subject_key=f"checkpoint:{checkpoint_seq}",
        payload={"merkle_root": root.hex(), "entry_count": entry_count},
    )

    await cur.execute(
        """
        INSERT INTO mnemos.chain_checkpoints
            (tenant_id, checkpoint_seq, merkle_root, shard_heads, entry_count, covers_through)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (tenant_id, checkpoint_seq, root, Json(shard_heads), entry_count, covers_through),
    )

    return Checkpoint(
        tenant_id=tenant_id,
        checkpoint_seq=checkpoint_seq,
        merkle_root=root,
        shard_heads=shard_heads,
        entry_count=entry_count,
        covers_through=covers_through,
    )


async def verify_chain(
    cur: psycopg.AsyncCursor,
    tenant_id: UUID,
    *,
    stop_at_first_break: bool = True,
) -> VerificationResult:
    """Recompute every hash in every shard, then every Merkle root.

    Two independent checks, because they catch different adversaries:

      * per-entry hashes catch an edited or deleted row;
      * checkpoint roots catch a whole shard rewritten with internally
        consistent hashes — a forgery the first check cannot see.
    """
    await cur.execute(
        "SELECT DISTINCT shard_id FROM mnemos.audit_chain WHERE tenant_id = %s ORDER BY shard_id",
        (tenant_id,),
    )
    shards = [int(row[0]) for row in await cur.fetchall()]

    checked = 0
    for shard in shards:
        await cur.execute(
            "SELECT seq, payload, payload_hash, prev_hash, entry_hash "
            "FROM mnemos.audit_chain WHERE tenant_id = %s AND shard_id = %s ORDER BY seq",
            (tenant_id, shard),
        )
        expected_prev = GENESIS_HASH
        expected_seq = 1

        for row in await cur.fetchall():
            seq, payload, stored_payload_hash, prev_hash, stored_entry_hash = row
            seq = int(seq)
            checked += 1

            # A gap means a row was removed. The surviving rows still chain to
            # each other, so only the sequence reveals it.
            if seq != expected_seq:
                return _broken(
                    tenant_id,
                    shard,
                    seq,
                    len(shards),
                    checked,
                    f"sequence gap: expected {expected_seq}, found {seq}",
                )

            recomputed = payload_hash(payload)
            if recomputed != bytes(stored_payload_hash):
                return _broken(
                    tenant_id,
                    shard,
                    seq,
                    len(shards),
                    checked,
                    "payload does not match its recorded hash — the row was edited",
                )

            if bytes(prev_hash) != expected_prev:
                return _broken(
                    tenant_id,
                    shard,
                    seq,
                    len(shards),
                    checked,
                    "prev_hash does not match the previous entry — the chain was spliced",
                )

            recomputed_entry = entry_hash(recomputed, expected_prev)
            if recomputed_entry != bytes(stored_entry_hash):
                return _broken(
                    tenant_id,
                    shard,
                    seq,
                    len(shards),
                    checked,
                    "entry_hash does not recompute",
                )

            expected_prev = recomputed_entry
            expected_seq = seq + 1

    await cur.execute(
        "SELECT checkpoint_seq, merkle_root, shard_heads FROM mnemos.chain_checkpoints "
        "WHERE tenant_id = %s ORDER BY checkpoint_seq",
        (tenant_id,),
    )
    checkpoints = await cur.fetchall()
    for checkpoint_seq, stored_root, shard_heads in checkpoints:
        heads = dict(shard_heads)

        # 1. The checkpoint must be internally consistent.
        leaves = [bytes.fromhex(str(v["hash"])) for v in heads.values()]
        if merkle_root(leaves) != bytes(stored_root):
            return _checkpoint_broken(
                tenant_id,
                checked,
                len(shards),
                len(checkpoints),
                f"checkpoint {checkpoint_seq}: Merkle root does not recompute from its "
                "own shard heads",
            )

        # 2. And — the check that actually catches a forger — the LIVE chain
        # must still agree with what the checkpoint recorded.
        #
        # Comparing a checkpoint against its own stored snapshot proves only
        # that the snapshot was not edited. An attacker who rewrites a whole
        # shard consistently leaves both the per-entry hashes and the snapshot
        # intact; the divergence appears only here, between the entry the chain
        # holds now and the hash the checkpoint committed to earlier.
        for shard_key, head in heads.items():
            shard_id = int(shard_key)
            head_seq = int(head["seq"])
            checkpointed = str(head["hash"])

            await cur.execute(
                "SELECT entry_hash FROM mnemos.audit_chain "
                "WHERE tenant_id = %s AND shard_id = %s AND seq = %s",
                (tenant_id, shard_id, head_seq),
            )
            live = await cur.fetchone()

            if live is None:
                return _checkpoint_broken(
                    tenant_id,
                    checked,
                    len(shards),
                    len(checkpoints),
                    f"checkpoint {checkpoint_seq}: entry {shard_id}/{head_seq} is missing "
                    "but was covered by a committed Merkle root",
                )

            if bytes(live[0]).hex() != checkpointed:
                return _checkpoint_broken(
                    tenant_id,
                    checked,
                    len(shards),
                    len(checkpoints),
                    f"checkpoint {checkpoint_seq}: shard {shard_id} diverges from the "
                    f"committed Merkle root at seq {head_seq} — the shard was rewritten "
                    "after the checkpoint was taken",
                )

    return VerificationResult(
        tenant_id=tenant_id,
        valid=True,
        entries_checked=checked,
        shards_checked=len(shards),
        checkpoints_checked=len(checkpoints),
    )


def _checkpoint_broken(
    tenant_id: UUID, checked: int, shards: int, checkpoints: int, detail: str
) -> VerificationResult:
    log.error("checkpoint mismatch", extra={"detail": detail})
    return VerificationResult(
        tenant_id=tenant_id,
        valid=False,
        entries_checked=checked,
        shards_checked=shards,
        checkpoints_checked=checkpoints,
        detail=detail,
    )


def _broken(
    tenant_id: UUID, shard: int, seq: int, shards: int, checked: int, detail: str
) -> VerificationResult:
    log.error("chain broken", extra={"shard": shard, "seq": seq, "detail": detail})
    return VerificationResult(
        tenant_id=tenant_id,
        valid=False,
        entries_checked=checked,
        shards_checked=shards,
        checkpoints_checked=0,
        broken_at=(shard, seq),
        detail=detail,
    )


__all__ = ["ChainBroken", "append_audit", "checkpoint", "verify_chain"]
