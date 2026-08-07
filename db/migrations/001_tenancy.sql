-- 001 — tenancy
--
-- Schema conventions used throughout (validated against the cockroachdb-sql
-- skill's anti-pattern checklist in Phase 02.9, ADR-009):
--
--   * UUID primary keys, never sequential integers. Sequential keys concentrate
--     writes on one range and turn a distributed database into a single hot node.
--   * tenant_id leads every composite primary key, so a tenant's data is
--     physically contiguous and range splits fall on tenant boundaries.
--   * TIMESTAMPTZ everywhere. A memory system that stores naive timestamps is
--     wrong the moment it crosses a region, which is the point of the product.
--   * No ON DELETE CASCADE on anything the Warden must delete deliberately.
--     Cascades hide what a deletion actually destroys; `forget` counts rows
--     before removing them, and the count is the user-facing preview.

CREATE SCHEMA IF NOT EXISTS mnemos;

CREATE TABLE IF NOT EXISTS mnemos.tenants (
    tenant_id           UUID        NOT NULL DEFAULT gen_random_uuid(),
    slug                STRING      NOT NULL,
    display_name        STRING      NOT NULL,

    -- Default home region for subjects with no explicit residency policy.
    -- Enforced in the read/write path by the Warden (Phase 06.2), and used as
    -- the REGIONAL BY ROW partition column on multi-region clusters (012).
    default_region      STRING      NOT NULL DEFAULT 'us-east-1',

    -- Trust promotion rules (Phase 05.4). Defaults are strict: agent- and
    -- external-sourced facts need two independent corroborations, where
    -- "independent" means a different session AND a different source_trust.
    promotion_policy    JSONB       NOT NULL DEFAULT '{
        "required_corroborations": 2,
        "require_distinct_session": true,
        "require_distinct_source_trust": true,
        "unverified_ttl_days": 30
    }'::JSONB,

    -- Two-person rule for destructive Warden operations (Phase 04.2).
    dual_control        BOOL        NOT NULL DEFAULT false,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_tenants PRIMARY KEY (tenant_id),
    CONSTRAINT uq_tenants_slug UNIQUE (slug)
);

CREATE TABLE IF NOT EXISTS mnemos.agents (
    tenant_id     UUID        NOT NULL,
    agent_id      UUID        NOT NULL DEFAULT gen_random_uuid(),
    name          STRING      NOT NULL,
    description   STRING,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_agents PRIMARY KEY (tenant_id, agent_id),
    CONSTRAINT fk_agents_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id),
    CONSTRAINT uq_agents_name UNIQUE (tenant_id, name)
);

-- API keys are stored hashed. The plaintext `mn_live_...` value exists exactly
-- once, at creation time, and is never recoverable — the same discipline the
-- CockroachDB Cloud console applies to SQL user passwords.
CREATE TABLE IF NOT EXISTS mnemos.api_keys (
    tenant_id     UUID        NOT NULL,
    key_id        UUID        NOT NULL DEFAULT gen_random_uuid(),
    key_hash      BYTES       NOT NULL,
    label         STRING      NOT NULL,

    -- read < write < admin. Only admin may reach the Warden, and the Custodian
    -- is deliberately capped at write (Phase 07.3) so it can never destroy.
    scope         STRING      NOT NULL,

    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ,

    CONSTRAINT pk_api_keys PRIMARY KEY (tenant_id, key_id),
    CONSTRAINT fk_api_keys_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id),
    CONSTRAINT ck_api_keys_scope CHECK (scope IN ('read', 'write', 'admin'))
);

-- Key lookup happens on every request and must not scan.
CREATE UNIQUE INDEX IF NOT EXISTS ix_api_keys_hash
    ON mnemos.api_keys (key_hash)
    STORING (tenant_id, scope, revoked_at);
