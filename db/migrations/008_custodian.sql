-- 008 — Custodian runs and findings
--
-- The Custodian's own activity is auditable like everything else. Its findings
-- enter semantic memory at source_trust='agent', meaning they are subject to
-- the same corroboration gate as any other model output — our own agent does
-- not get to believe itself on the first pass (Phase 07.3).

CREATE TABLE IF NOT EXISTS mnemos.custodian_runs (
    tenant_id       UUID        NOT NULL,
    run_id          UUID        NOT NULL DEFAULT gen_random_uuid(),

    trigger_source  STRING      NOT NULL,   -- schedule | alarm | manual
    trigger_detail  STRING,

    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          STRING      NOT NULL DEFAULT 'running',

    -- Coverage honesty (Phase 07.1): diagnostics with no MCP equivalent are
    -- skipped and COUNTED. Silent partial coverage reads as full coverage,
    -- which is the failure mode Phase 10.7 exists to prevent.
    skills_run      INT8        NOT NULL DEFAULT 0,
    checks_run      INT8        NOT NULL DEFAULT 0,
    checks_skipped  INT8        NOT NULL DEFAULT 0,
    skipped_detail  JSONB,

    CONSTRAINT pk_custodian_runs PRIMARY KEY (tenant_id, run_id),
    CONSTRAINT fk_custodian_runs_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id),
    CONSTRAINT ck_custodian_status
        CHECK (status IN ('running', 'succeeded', 'failed', 'partial')),
    CONSTRAINT ck_custodian_trigger
        CHECK (trigger_source IN ('schedule', 'alarm', 'manual'))
);

CREATE TABLE IF NOT EXISTS mnemos.custodian_findings (
    tenant_id      UUID        NOT NULL,
    run_id         UUID        NOT NULL,
    finding_id     UUID        NOT NULL DEFAULT gen_random_uuid(),

    severity       STRING      NOT NULL,
    summary        STRING      NOT NULL,
    evidence       JSONB       NOT NULL,
    recommendation STRING,

    -- Which official CockroachDB Agent Skill produced this, and which tool
    -- surfaced the data. Both are first-class UI elements in the console —
    -- a judge should see the sponsor's own skills working, and see which
    -- findings came from the Cloud MCP Server versus the ccloud CLI.
    skill_id       STRING      NOT NULL,
    tool_source    STRING      NOT NULL,   -- mcp | ccloud

    -- Set once this finding has been distilled into semantic memory.
    fact_id        UUID,

    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_custodian_findings PRIMARY KEY (tenant_id, run_id, finding_id),
    CONSTRAINT fk_custodian_findings_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id),
    CONSTRAINT ck_finding_severity
        CHECK (severity IN ('info', 'warn', 'critical')),
    CONSTRAINT ck_finding_tool_source
        CHECK (tool_source IN ('mcp', 'ccloud'))
);

CREATE INDEX IF NOT EXISTS ix_findings_severity
    ON mnemos.custodian_findings (tenant_id, severity, created_at DESC);
