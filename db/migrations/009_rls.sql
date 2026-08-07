-- 009 — Row-Level Security
--
-- The API resolves an API key to a tenant and sets app.tenant_id. RLS is the
-- backstop *beneath* that: if the middleware is bypassed, misconfigured, or
-- simply wrong, the database still refuses.
--
-- Defense in depth is only real if the second layer is verified alone, so
-- Phase 10.2 deliberately disables the middleware and re-runs the exfiltration
-- suite against RLS by itself.
--
-- Note on roles: FORCE ROW LEVEL SECURITY makes the policy apply to the table
-- owner too. Without it, the owner silently bypasses every policy — which is
-- how RLS ends up being decorative in a lot of deployments.

CREATE OR REPLACE FUNCTION mnemos.current_tenant() RETURNS UUID AS $$
DECLARE
    raw TEXT;
BEGIN
    raw := current_setting('app.tenant_id', true);
    IF raw IS NULL OR raw = '' THEN
        RETURN NULL;
    END IF;
    RETURN raw::UUID;
END;
$$ LANGUAGE PLPGSQL;

ALTER TABLE mnemos.episodic_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemos.episodic_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON mnemos.episodic_events
    USING (tenant_id = mnemos.current_tenant())
    WITH CHECK (tenant_id = mnemos.current_tenant());

ALTER TABLE mnemos.semantic_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemos.semantic_facts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON mnemos.semantic_facts
    USING (tenant_id = mnemos.current_tenant())
    WITH CHECK (tenant_id = mnemos.current_tenant());

ALTER TABLE mnemos.fact_provenance ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemos.fact_provenance FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON mnemos.fact_provenance
    USING (tenant_id = mnemos.current_tenant())
    WITH CHECK (tenant_id = mnemos.current_tenant());

ALTER TABLE mnemos.recall_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemos.recall_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON mnemos.recall_log
    USING (tenant_id = mnemos.current_tenant())
    WITH CHECK (tenant_id = mnemos.current_tenant());

ALTER TABLE mnemos.action_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemos.action_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON mnemos.action_log
    USING (tenant_id = mnemos.current_tenant())
    WITH CHECK (tenant_id = mnemos.current_tenant());

ALTER TABLE mnemos.action_recalls ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemos.action_recalls FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON mnemos.action_recalls
    USING (tenant_id = mnemos.current_tenant())
    WITH CHECK (tenant_id = mnemos.current_tenant());

ALTER TABLE mnemos.skill_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemos.skill_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON mnemos.skill_versions
    USING (tenant_id = mnemos.current_tenant())
    WITH CHECK (tenant_id = mnemos.current_tenant());

ALTER TABLE mnemos.skill_provenance ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemos.skill_provenance FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON mnemos.skill_provenance
    USING (tenant_id = mnemos.current_tenant())
    WITH CHECK (tenant_id = mnemos.current_tenant());

-- The ledger is tenant-scoped too. An audit trail readable across tenants would
-- leak subject keys and operation timing — which is a side channel, not an
-- audit trail.
ALTER TABLE mnemos.audit_chain ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemos.audit_chain FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON mnemos.audit_chain
    USING (tenant_id = mnemos.current_tenant())
    WITH CHECK (tenant_id = mnemos.current_tenant());

ALTER TABLE mnemos.legal_holds ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemos.legal_holds FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON mnemos.legal_holds
    USING (tenant_id = mnemos.current_tenant())
    WITH CHECK (tenant_id = mnemos.current_tenant());

ALTER TABLE mnemos.region_crossings ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemos.region_crossings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON mnemos.region_crossings
    USING (tenant_id = mnemos.current_tenant())
    WITH CHECK (tenant_id = mnemos.current_tenant());
