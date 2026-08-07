-- 006 — the governance plane's storage
--
-- Residency policies, legal holds, region crossings, revocations, and the
-- Custodian's governance proposals. Only the Warden writes here (Phase 06), and
-- only the Warden holds DELETE anywhere in the schema.

-- Where a subject's rows must live, and what may leave that jurisdiction.
CREATE TABLE IF NOT EXISTS mnemos.residency_policies (
    tenant_id        UUID        NOT NULL,
    subject_pattern  STRING      NOT NULL,   -- e.g. 'patient:*', 'user:eu:*'
    home_region      STRING      NOT NULL,

    -- What may cross a border for this subject class:
    --   none      nothing leaves; foreign requests are refused
    --   derived   policy-approved derived facts only; raw content never leaves
    --   aggregate statistics only, no individual facts
    projection       STRING      NOT NULL DEFAULT 'derived',

    priority         INT8        NOT NULL DEFAULT 100,  -- lower wins
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_residency PRIMARY KEY (tenant_id, subject_pattern),
    CONSTRAINT fk_residency_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id),
    CONSTRAINT ck_residency_projection
        CHECK (projection IN ('none', 'derived', 'aggregate'))
);

-- A block on erasure citing an external matter. Outranks erasure, pins episodes
-- against TTL, and extends the GC window so recall_as_of() covers the retention
-- obligation. `forget` against a held subject fails LOUDLY with these fields —
-- a compliance officer needs all of them (Phase 06.3).
CREATE TABLE IF NOT EXISTS mnemos.legal_holds (
    tenant_id         UUID        NOT NULL,
    hold_id           UUID        NOT NULL DEFAULT gen_random_uuid(),
    subject_key       STRING      NOT NULL,

    matter_reference  STRING      NOT NULL,
    placed_by         STRING      NOT NULL,
    placed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at        TIMESTAMPTZ,

    released_by       STRING,
    released_at       TIMESTAMPTZ,
    release_reason    STRING,

    CONSTRAINT pk_legal_holds PRIMARY KEY (tenant_id, hold_id),
    CONSTRAINT fk_holds_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id)
);

-- The hot path: "is this subject held?" is checked before every erasure.
CREATE INDEX IF NOT EXISTS ix_holds_active
    ON mnemos.legal_holds (tenant_id, subject_key)
    WHERE released_at IS NULL;

-- Every attempt to move data across a jurisdiction, allowed or refused. Denials
-- are logged exactly as loudly as permissions — a residency system that only
-- records its successes cannot be audited.
CREATE TABLE IF NOT EXISTS mnemos.region_crossings (
    tenant_id      UUID        NOT NULL,
    crossing_id    UUID        NOT NULL DEFAULT gen_random_uuid(),

    subject_key    STRING      NOT NULL,
    from_region    STRING      NOT NULL,
    to_region      STRING      NOT NULL,
    projection     STRING      NOT NULL,
    policy_applied STRING      NOT NULL,
    allowed        BOOL        NOT NULL,
    denied_reason  STRING,

    requested_by   STRING,
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_region_crossings PRIMARY KEY (tenant_id, crossing_id),
    CONSTRAINT fk_crossings_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id)
);

CREATE INDEX IF NOT EXISTS ix_crossings_recent
    ON mnemos.region_crossings (tenant_id, occurred_at DESC);

-- A revocation and the full blast radius it covered. The manifest is retained
-- forever: "what did we revoke, and what did it touch" is the question asked
-- months later, by someone who was not there.
CREATE TABLE IF NOT EXISTS mnemos.revocations (
    tenant_id        UUID        NOT NULL,
    revocation_id    UUID        NOT NULL DEFAULT gen_random_uuid(),

    source_event_id  UUID,
    source_selector  STRING,
    reason           STRING      NOT NULL,
    actor            STRING      NOT NULL,

    -- {facts: n, corroborations: n, skills: n, recalls: n, actions: n, ids: {...}}
    radius_manifest  JSONB       NOT NULL,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_revocations PRIMARY KEY (tenant_id, revocation_id),
    CONSTRAINT fk_revocations_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id)
);

-- The Custodian may notice that something ought to be destroyed. It may say so.
-- It may never act (Phase 07.4). The agent can ask; only a person can answer.
CREATE TABLE IF NOT EXISTS mnemos.governance_proposals (
    tenant_id     UUID        NOT NULL,
    proposal_id   UUID        NOT NULL DEFAULT gen_random_uuid(),

    proposed_by   STRING      NOT NULL,
    kind          STRING      NOT NULL,   -- forget | revoke | quarantine | hold | policy
    target        STRING      NOT NULL,
    rationale     STRING      NOT NULL,
    evidence      JSONB,

    status        STRING      NOT NULL DEFAULT 'pending',
    decided_by    STRING,
    decided_at    TIMESTAMPTZ,
    decision_note STRING,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_proposals PRIMARY KEY (tenant_id, proposal_id),
    CONSTRAINT fk_proposals_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id),
    CONSTRAINT ck_proposals_status
        CHECK (status IN ('pending', 'approved', 'rejected', 'executed')),
    CONSTRAINT ck_proposals_kind
        CHECK (kind IN ('forget', 'revoke', 'quarantine', 'hold', 'policy'))
);

CREATE INDEX IF NOT EXISTS ix_proposals_pending
    ON mnemos.governance_proposals (tenant_id, created_at DESC)
    WHERE status = 'pending';
