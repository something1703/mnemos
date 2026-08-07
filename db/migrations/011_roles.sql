-- 011 — service roles and grants
--
-- INVARIANT 1, EXPRESSED AS PRIVILEGES.
--
-- "No LLM-driven process ever holds DELETE" is not a code review rule here; it
-- is the absence of a GRANT. The API, the sleep cycle, and the Custodian all
-- run as roles that were never given DELETE on anything, so a prompt injection
-- that reaches SQL still cannot destroy a row. Only mnemos_warden can, and the
-- Warden has no model in it (ADR-002).
--
-- Roles are created without LOGIN. db/scripts/provision_users.py creates login
-- users with generated passwords and grants them these roles, so no credential
-- ever appears in a migration file.
--
-- Note the deliberate omissions below. They are the security control:
--   mnemos_api       — SELECT, INSERT, UPDATE.  No DELETE. Anywhere.
--   mnemos_pipeline  — same, plus BYPASSRLS for cross-tenant consolidation.
--   mnemos_readonly  — SELECT only. The Custodian's fallback if direct SQL is
--                      ever needed; its default path is MCP-only.
--   mnemos_warden    — the only role with DELETE.

CREATE ROLE IF NOT EXISTS mnemos_api;
CREATE ROLE IF NOT EXISTS mnemos_pipeline;
CREATE ROLE IF NOT EXISTS mnemos_readonly;
CREATE ROLE IF NOT EXISTS mnemos_warden;

GRANT USAGE ON SCHEMA mnemos TO mnemos_api, mnemos_pipeline, mnemos_readonly, mnemos_warden;

-- ---------------------------------------------------------------------------
-- mnemos_api — serves remember/recall/explain. Writes memory, never removes it.
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON TABLE
    mnemos.episodic_events,
    mnemos.semantic_facts,
    mnemos.fact_provenance,
    mnemos.recall_log,
    mnemos.action_log,
    mnemos.action_recalls,
    mnemos.skills,
    mnemos.skill_versions,
    mnemos.skill_outcomes,
    mnemos.skill_provenance,
    mnemos.audit_chain,
    mnemos.chain_heads
TO mnemos_api;

GRANT SELECT ON TABLE
    mnemos.tenants,
    mnemos.agents,
    mnemos.api_keys,
    mnemos.residency_policies,
    mnemos.legal_holds,
    mnemos.region_crossings,
    mnemos.revocations,
    mnemos.chain_checkpoints,
    mnemos.custodian_runs,
    mnemos.custodian_findings,
    mnemos.governance_proposals
TO mnemos_api;

-- The API records attempted and refused border crossings.
GRANT INSERT ON TABLE mnemos.region_crossings TO mnemos_api;

-- ---------------------------------------------------------------------------
-- mnemos_pipeline — the sleep cycle. Invokes Bedrock, so it is untrusted by
-- construction: everything it writes lands 'unverified'. Still no DELETE —
-- decay lowers strength, it never removes a row.
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON TABLE
    mnemos.episodic_events,
    mnemos.semantic_facts,
    mnemos.fact_provenance,
    mnemos.skill_versions,
    mnemos.skill_provenance,
    mnemos.audit_chain,
    mnemos.chain_heads,
    mnemos.chain_checkpoints
TO mnemos_pipeline;

GRANT SELECT ON TABLE
    mnemos.tenants,
    mnemos.agents,
    mnemos.recall_log,
    mnemos.residency_policies,
    mnemos.legal_holds
TO mnemos_pipeline;

-- Consolidation runs across tenants in one sweep, so it must see past RLS.
-- This is the single most privileged thing the pipeline holds, which is why it
-- still has no DELETE: bypassing isolation and being able to destroy are very
-- different powers, and combining them would be the whole ballgame.
ALTER ROLE mnemos_pipeline BYPASSRLS;

-- ---------------------------------------------------------------------------
-- mnemos_readonly — the Custodian's fallback. Reads diagnostics, nothing more.
-- ---------------------------------------------------------------------------
GRANT SELECT ON TABLE
    mnemos.tenants,
    mnemos.agents,
    mnemos.custodian_runs,
    mnemos.custodian_findings,
    mnemos.chain_checkpoints
TO mnemos_readonly;

-- The Custodian files findings and proposals. It may ask; it may never act.
GRANT INSERT ON TABLE
    mnemos.custodian_runs,
    mnemos.custodian_findings,
    mnemos.governance_proposals
TO mnemos_readonly;

GRANT UPDATE ON TABLE mnemos.custodian_runs TO mnemos_readonly;

-- ---------------------------------------------------------------------------
-- mnemos_warden — the only role that may destroy anything.
-- ---------------------------------------------------------------------------
GRANT ALL ON SCHEMA mnemos TO mnemos_warden;
GRANT ALL ON TABLE
    mnemos.episodic_events,
    mnemos.semantic_facts,
    mnemos.fact_provenance,
    mnemos.recall_log,
    mnemos.action_log,
    mnemos.action_recalls,
    mnemos.skills,
    mnemos.skill_versions,
    mnemos.skill_outcomes,
    mnemos.skill_provenance,
    mnemos.audit_chain,
    mnemos.chain_heads,
    mnemos.chain_checkpoints,
    mnemos.residency_policies,
    mnemos.legal_holds,
    mnemos.region_crossings,
    mnemos.revocations,
    mnemos.governance_proposals,
    mnemos.tenants,
    mnemos.agents,
    mnemos.api_keys,
    mnemos.custodian_runs,
    mnemos.custodian_findings
TO mnemos_warden;
