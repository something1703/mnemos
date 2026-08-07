-- 002 — episodic tier
--
-- Raw experience. Immutable once written, encrypted at rest with a per-tenant
-- KMS-wrapped data key, homed to a jurisdiction, and decayed by Row-Level TTL.
-- Everything else in Mnemos is derived from these rows, which is why invariant 3
-- (no fact without provenance to an episode) is expressible at all.

CREATE TABLE IF NOT EXISTS mnemos.episodic_events (
    tenant_id           UUID        NOT NULL,
    subject_key         STRING      NOT NULL,
    event_id            UUID        NOT NULL DEFAULT gen_random_uuid(),

    -- Physical jurisdiction. Derived from the subject's residency policy, NOT
    -- from where the writer happens to be running (invariant 4). On multi-region
    -- clusters migration 012 promotes this to the REGIONAL BY ROW partition key.
    home_region         STRING      NOT NULL,

    session_id          UUID        NOT NULL,
    agent_id            UUID,
    event_type          STRING      NOT NULL,

    -- Envelope encryption: the data key is generated per row, wrapped by the
    -- tenant's KMS CMK, and stored beside the ciphertext. The plaintext key is
    -- never persisted. Destroying the CMK renders every row unreadable —
    -- including copies inside backups and MVCC history, which is what makes
    -- `shred` mean something (Phase 06.4).
    content_ciphertext  BYTES       NOT NULL,
    content_dek_wrapped BYTES       NOT NULL,

    -- SHA-256 of the plaintext. Lets a deposition prove which bytes a fact was
    -- derived from without decrypting them, and survives erasure of the content
    -- itself under `redact`.
    content_hash        BYTES       NOT NULL,

    -- Required, never optional. This single field is what makes the
    -- corroboration gate and poisoning defense possible (Phase 05.4). Callers
    -- get the least-trusted value their key scope allows, by default.
    source_trust        STRING      NOT NULL,

    s3_artifact         STRING,
    idempotency_key     STRING,

    consolidated_at     TIMESTAMPTZ,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Row-Level TTL. NULL means "never expire" — which is how the Warden pins
    -- episodes under legal hold (Phase 06.3). The TTL job must never be the
    -- reason evidence disappears during an open matter.
    expire_at           TIMESTAMPTZ,

    -- subject_key precedes event_id so one subject's whole history is
    -- contiguous: recall, forget, and blast radius all scan by subject.
    CONSTRAINT pk_episodic PRIMARY KEY (tenant_id, subject_key, event_id),
    CONSTRAINT fk_episodic_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id),
    CONSTRAINT ck_episodic_source_trust
        CHECK (source_trust IN ('system', 'operator', 'agent', 'external'))
) WITH (ttl_expiration_expression = 'expire_at', ttl_job_cron = '@hourly');

-- Idempotency: a duplicate remember() returns the original event_id and writes
-- no new rows and no new audit entry (Phase 03.2).
CREATE UNIQUE INDEX IF NOT EXISTS ix_episodic_idempotency
    ON mnemos.episodic_events (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- The session tail served on every recall.
CREATE INDEX IF NOT EXISTS ix_episodic_session
    ON mnemos.episodic_events (tenant_id, session_id, occurred_at DESC);

-- The consolidation sweep: oldest unconsolidated first, grouped by session.
CREATE INDEX IF NOT EXISTS ix_episodic_unconsolidated
    ON mnemos.episodic_events (tenant_id, occurred_at)
    WHERE consolidated_at IS NULL;

-- Residency reporting (`where_is`) and region-scoped consolidation batching.
CREATE INDEX IF NOT EXISTS ix_episodic_region
    ON mnemos.episodic_events (tenant_id, home_region);
