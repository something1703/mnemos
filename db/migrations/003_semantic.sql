-- 003 — semantic tier
--
-- Distilled, durable claims. Carries the embedding, the trust state, and the
-- supersession/contest relationships. This is the table the vector index lives
-- on, and the table `forget` must delete atomically together with its index
-- entries — the guarantee a separate vector store cannot make.

CREATE TABLE IF NOT EXISTS mnemos.semantic_facts (
    tenant_id           UUID        NOT NULL,
    fact_id             UUID        NOT NULL DEFAULT gen_random_uuid(),

    home_region         STRING      NOT NULL,
    subject_key         STRING      NOT NULL,
    fact_kind           STRING      NOT NULL,

    text_ciphertext     BYTES       NOT NULL,
    text_dek_wrapped    BYTES       NOT NULL,
    text_hash           BYTES       NOT NULL,

    -- Titan Embed v2, 1024 dimensions. Computed pre-encryption because it must
    -- be searchable. That makes the embedding a lossy but non-zero leak of the
    -- source text — documented in docs/limits.md, and precisely why erasure has
    -- to delete the vector in the same transaction as the row.
    embedding           VECTOR(1024),
    tsv                 TSVECTOR,

    -- The trust lattice (Phase 05.4). Everything an LLM writes starts
    -- 'unverified' and is excluded from recall until independently corroborated.
    trust               STRING      NOT NULL DEFAULT 'unverified',

    strength            FLOAT8      NOT NULL DEFAULT 1.0,
    confidence          FLOAT8      NOT NULL DEFAULT 0.5,
    corroboration_count INT8        NOT NULL DEFAULT 0,

    recall_count        INT8        NOT NULL DEFAULT 0,
    last_recalled_at    TIMESTAMPTZ,

    -- Displaced by better evidence. Never deleted: supersession history is
    -- exactly what a deposition needs to explain a past decision.
    superseded_by       UUID,
    -- Contradicted with comparable evidence. recall() returns both sides rather
    -- than silently picking a winner.
    contested_with      UUID,

    quarantined_at      TIMESTAMPTZ,
    quarantine_reason   STRING,
    revoked_at          TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_facts PRIMARY KEY (tenant_id, fact_id),
    CONSTRAINT fk_facts_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id),
    CONSTRAINT ck_facts_trust
        CHECK (trust IN ('unverified', 'corroborated', 'trusted', 'contested', 'quarantined')),
    CONSTRAINT ck_facts_strength CHECK (strength >= 0.1),
    CONSTRAINT ck_facts_confidence CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

-- C-SPANN, prefix-scoped by tenant_id.
--
-- The prefix is the isolation boundary: an approximate-nearest-neighbour search
-- is partitioned per tenant *inside the index*, so a crafted vector query cannot
-- surface another tenant's neighbours. Scoping only in the WHERE clause is the
-- mistake most vector-backed systems make, and Phase 10.2 attacks it directly.
CREATE VECTOR INDEX IF NOT EXISTS ix_facts_embedding
    ON mnemos.semantic_facts (tenant_id, embedding);

-- The lexical half of hybrid recall, fused with vectors via RRF (Phase 03.3).
-- Vectors miss exact identifiers and rare tokens — drug names, error codes,
-- account numbers — which is exactly the content this product carries.
CREATE INVERTED INDEX IF NOT EXISTS ix_facts_tsv
    ON mnemos.semantic_facts (tenant_id, tsv);

CREATE INDEX IF NOT EXISTS ix_facts_subject
    ON mnemos.semantic_facts (tenant_id, subject_key, trust);

-- The decay sweep (Phase 05.5) and the unverified-backlog alarm, which is the
-- leading indicator of a poisoning attempt (Phase 11.4).
CREATE INDEX IF NOT EXISTS ix_facts_trust_recency
    ON mnemos.semantic_facts (tenant_id, trust, last_recalled_at);
