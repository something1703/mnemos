-- 005 — procedural tier
--
-- Learned playbooks, versioned, with fitness counters.
--
-- This is the most dangerous tier in any agentic memory system: a skill is not
-- a belief, it is an instruction the agent will execute. An agent that can teach
-- itself an unvetted procedure and then run it has no meaningful security
-- boundary at all. So agent- and external-authored skill versions land
-- 'quarantined' and find_skill() will not return them until corroborated
-- (Phase 03.7).

CREATE TABLE IF NOT EXISTS mnemos.skills (
    tenant_id    UUID        NOT NULL,
    skill_id     UUID        NOT NULL DEFAULT gen_random_uuid(),
    name         STRING      NOT NULL,
    description  STRING,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_skills PRIMARY KEY (tenant_id, skill_id),
    CONSTRAINT fk_skills_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id),
    CONSTRAINT uq_skills_name UNIQUE (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS mnemos.skill_versions (
    tenant_id            UUID        NOT NULL,
    skill_id             UUID        NOT NULL,
    version              INT8        NOT NULL,

    playbook_ciphertext  BYTES       NOT NULL,
    playbook_dek_wrapped BYTES       NOT NULL,
    playbook_hash        BYTES       NOT NULL,

    -- Matched against an incoming task description, so a paraphrased problem
    -- still finds the playbook that solved it.
    task_embedding       VECTOR(1024),

    source_trust         STRING      NOT NULL,
    trust                STRING      NOT NULL DEFAULT 'quarantined',

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    quarantined_at       TIMESTAMPTZ,
    revoked_at           TIMESTAMPTZ,

    CONSTRAINT pk_skill_versions PRIMARY KEY (tenant_id, skill_id, version),
    CONSTRAINT fk_skill_versions_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id),
    CONSTRAINT ck_skill_source_trust
        CHECK (source_trust IN ('system', 'operator', 'agent', 'external')),
    CONSTRAINT ck_skill_trust
        CHECK (trust IN ('unverified', 'corroborated', 'trusted', 'contested', 'quarantined'))
);

CREATE VECTOR INDEX IF NOT EXISTS ix_skill_task_embedding
    ON mnemos.skill_versions (tenant_id, task_embedding);

-- Fitness. Repeated failure demotes trust automatically (Phase 03.7): a
-- playbook that keeps not working should stop being offered without anyone
-- having to notice.
CREATE TABLE IF NOT EXISTS mnemos.skill_outcomes (
    tenant_id   UUID        NOT NULL,
    skill_id    UUID        NOT NULL,
    version     INT8        NOT NULL,
    outcome_id  UUID        NOT NULL DEFAULT gen_random_uuid(),

    success     BOOL        NOT NULL,
    latency_ms  INT8,
    notes       STRING,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_skill_outcomes PRIMARY KEY (tenant_id, skill_id, version, outcome_id),
    CONSTRAINT fk_skill_outcomes_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id)
);

-- Skills cite facts. When a source is revoked, any skill resting on the
-- contaminated facts must be caught by blast_radius (Phase 03.6) — this edge
-- table is how the closure reaches the procedural tier.
CREATE TABLE IF NOT EXISTS mnemos.skill_provenance (
    tenant_id  UUID NOT NULL,
    skill_id   UUID NOT NULL,
    version    INT8 NOT NULL,
    fact_id    UUID NOT NULL,

    CONSTRAINT pk_skill_provenance PRIMARY KEY (tenant_id, skill_id, version, fact_id),
    CONSTRAINT fk_skill_provenance_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id)
);

CREATE INDEX IF NOT EXISTS ix_skill_provenance_by_fact
    ON mnemos.skill_provenance (tenant_id, fact_id);
