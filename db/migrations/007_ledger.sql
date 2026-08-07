-- 007 — the hash-chained audit ledger
--
-- Why sharded: the obvious design is one chain per tenant, where every write
-- reads `max(seq) FOR UPDATE`. That serialises every write in the tenant onto
-- one row and becomes the system's throughput ceiling — an embarrassing
-- property for a distributed database. So chains are sharded by subject key:
-- a subject's history stays one totally-ordered chain (which is what auditors
-- and depositions need), while tenant-wide throughput scales with shard count.
--
-- Why checkpoints: sharding costs a single root of trust. A Merkle root over
-- all shard heads, committed each epoch, restores it — and anchoring that root
-- to S3 Object Lock (ADR-010) is what makes tampering detectable by someone who
-- does not trust our database administrator.
--
-- Hash construction, specified byte-for-byte in docs/ledger.md so the verifier
-- is reimplementable in any language:
--     payload_hash = sha256(canonical_json(payload))
--     entry_hash   = sha256(payload_hash || prev_hash)
-- Genesis rows use 32 zero bytes as prev_hash.

CREATE TABLE IF NOT EXISTS mnemos.audit_chain (
    tenant_id     UUID        NOT NULL,
    shard_id      INT2        NOT NULL,
    seq           INT8        NOT NULL,

    -- The transaction-local token that migration 010's trigger checks. Every
    -- mutation must present a ticket matching a row here, written in the SAME
    -- transaction — which is invariant 2, enforced by the database rather than
    -- by anyone remembering to call the right function.
    ticket        UUID        NOT NULL DEFAULT gen_random_uuid(),

    op            STRING      NOT NULL,
    subject_key   STRING,
    actor         STRING      NOT NULL,
    reason        STRING,

    payload       JSONB       NOT NULL,
    payload_hash  BYTES       NOT NULL,
    prev_hash     BYTES       NOT NULL,
    entry_hash    BYTES       NOT NULL,

    committed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_audit_chain PRIMARY KEY (tenant_id, shard_id, seq),
    CONSTRAINT fk_audit_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id),
    CONSTRAINT ck_audit_op CHECK (op IN (
        'remember', 'consolidate', 'reinforce', 'promote', 'demote',
        'recall', 'decay', 'supersede', 'contest',
        'quarantine', 'revoke', 'redact', 'forget', 'shred',
        'hold', 'release_hold', 'policy', 'checkpoint', 'learn_skill',
        'record_action', 'proposal'
    ))
);

-- The trigger resolves a ticket on every protected mutation, so this lookup is
-- on the hot path of every write in the system. UUID tickets distribute evenly,
-- so a global unique index here does not create a hotspot.
CREATE UNIQUE INDEX IF NOT EXISTS ix_audit_ticket
    ON mnemos.audit_chain (ticket);

-- Verifier and console paging walk a subject's history in order.
CREATE INDEX IF NOT EXISTS ix_audit_subject
    ON mnemos.audit_chain (tenant_id, subject_key, committed_at DESC);

CREATE INDEX IF NOT EXISTS ix_audit_recent
    ON mnemos.audit_chain (tenant_id, committed_at DESC);

-- One row per (tenant, shard), locked FOR UPDATE while appending. Keeping the
-- head in its own tiny table means the lock is on a row that holds nothing else,
-- rather than on the tail of a growing chain.
CREATE TABLE IF NOT EXISTS mnemos.chain_heads (
    tenant_id   UUID        NOT NULL,
    shard_id    INT2        NOT NULL,
    seq         INT8        NOT NULL DEFAULT 0,
    entry_hash  BYTES       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_chain_heads PRIMARY KEY (tenant_id, shard_id),
    CONSTRAINT fk_chain_heads_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id)
);

-- A Merkle root binding every shard head at a point in time. `mnemos-attest`
-- fetches the anchored copy from S3 and compares — if someone rewrote a shard
-- in the database, even consistently, the roots diverge and the tool says where.
CREATE TABLE IF NOT EXISTS mnemos.chain_checkpoints (
    tenant_id       UUID        NOT NULL,
    checkpoint_seq  INT8        NOT NULL,

    merkle_root     BYTES       NOT NULL,
    -- {"0": {"seq": 1471, "hash": "..."}, "1": {...}, ...}
    shard_heads     JSONB       NOT NULL,
    entry_count     INT8        NOT NULL,

    -- Populated once the root is written to the Object Lock bucket. A checkpoint
    -- with a NULL anchor_uri is not yet evidence of anything, and the console
    -- must not present it as though it were.
    anchor_uri      STRING,
    anchored_at     TIMESTAMPTZ,

    covers_through  TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_checkpoints PRIMARY KEY (tenant_id, checkpoint_seq),
    CONSTRAINT fk_checkpoints_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id)
);

-- "Is our proof current?" — alarmed on in Phase 11.4, because a checkpoint that
-- silently stopped running turns the whole ledger into an unanchored claim.
CREATE INDEX IF NOT EXISTS ix_checkpoints_unanchored
    ON mnemos.chain_checkpoints (tenant_id, created_at)
    WHERE anchored_at IS NULL;
