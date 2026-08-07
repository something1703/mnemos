-- 004 — the accountability graph
--
-- Four tables that turn "the agent knows things" into "we can prove why the
-- agent did that". This is the Accountability pillar's storage layer, and the
-- input to both explain() and blast_radius().
--
--   fact_provenance  fact  <- episode      (why we believe it)
--   recall_log       recall -> facts       (what the agent was told)
--   action_log       action                (what the agent then did)
--   action_recalls   action -> recalls     (the causal link)

-- Invariant 3 lives here: a fact with zero provenance edges is a bug, not a
-- belief. Enforced by the trigger in migration 010.
CREATE TABLE IF NOT EXISTS mnemos.fact_provenance (
    tenant_id    UUID        NOT NULL,
    fact_id      UUID        NOT NULL,
    event_id     UUID        NOT NULL,
    subject_key  STRING      NOT NULL,

    weight       FLOAT8      NOT NULL DEFAULT 1.0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_provenance PRIMARY KEY (tenant_id, fact_id, event_id),
    CONSTRAINT fk_provenance_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id)
);

-- Reverse traversal: given a poisoned episode, which facts derive from it?
-- This index is what keeps blast_radius under the 2s target at 100k facts.
CREATE INDEX IF NOT EXISTS ix_provenance_by_event
    ON mnemos.fact_provenance (tenant_id, event_id);

-- Every recall is recorded: which facts went to which agent, when, at what
-- score. Append-only. Without this table explain() is impossible, and "what did
-- the agent believe at 14:32" has no answer.
CREATE TABLE IF NOT EXISTS mnemos.recall_log (
    tenant_id       UUID        NOT NULL,
    recall_id       UUID        NOT NULL DEFAULT gen_random_uuid(),
    fact_id         UUID        NOT NULL,

    agent_id        UUID,
    session_id      UUID,
    query_hash      BYTES       NOT NULL,

    -- The score is returned decomposed rather than as one number, so ranking is
    -- inspectable instead of magic — and so a deposition can show the trust
    -- state a fact held at the moment it was used, not the state it holds now.
    similarity      FLOAT8,
    strength_at     FLOAT8,
    confidence_at   FLOAT8,
    trust_at        STRING,
    score           FLOAT8,

    -- Set when revoke_source() finds this recall inside a blast radius.
    contaminated_at TIMESTAMPTZ,

    recalled_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_recall_log PRIMARY KEY (tenant_id, recall_id, fact_id),
    CONSTRAINT fk_recall_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id)
);

CREATE INDEX IF NOT EXISTS ix_recall_by_fact
    ON mnemos.recall_log (tenant_id, fact_id, recalled_at DESC);

CREATE INDEX IF NOT EXISTS ix_recall_by_session
    ON mnemos.recall_log (tenant_id, session_id, recalled_at DESC);

-- What the agent did, declared with the recalls that caused it. The bridge
-- between memory and consequence — and the reason a revocation can reach
-- forward into decisions that were already made.
CREATE TABLE IF NOT EXISTS mnemos.action_log (
    tenant_id       UUID        NOT NULL,
    action_id       UUID        NOT NULL DEFAULT gen_random_uuid(),

    agent_id        UUID,
    session_id      UUID,
    subject_key     STRING,
    action_type     STRING      NOT NULL,
    description     STRING      NOT NULL,

    -- After revoke_source(), explain() on this action reports: "this decision
    -- was influenced by subsequently-revoked memory."
    contaminated_at TIMESTAMPTZ,
    contaminated_by UUID,

    declared_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_action_log PRIMARY KEY (tenant_id, action_id),
    CONSTRAINT fk_action_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id)
);

CREATE INDEX IF NOT EXISTS ix_action_contaminated
    ON mnemos.action_log (tenant_id, contaminated_at)
    WHERE contaminated_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS mnemos.action_recalls (
    tenant_id  UUID NOT NULL,
    action_id  UUID NOT NULL,
    recall_id  UUID NOT NULL,

    CONSTRAINT pk_action_recalls PRIMARY KEY (tenant_id, action_id, recall_id),
    CONSTRAINT fk_action_recalls_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id)
);

CREATE INDEX IF NOT EXISTS ix_action_recalls_by_recall
    ON mnemos.action_recalls (tenant_id, recall_id);
