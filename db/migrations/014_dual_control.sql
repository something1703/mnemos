-- 014 — dual control: two distinct admin keys for destructive Warden operations
--
-- mnemos.tenants.dual_control (migration 001) has existed since Phase 04.2
-- planning and mnemos_warden.errors.DualControlRequired has existed since
-- Phase 06.1, but nothing in the request path ever read the column or raised
-- the exception — dual control was schema and a docstring, not behaviour.
--
-- This table is the missing piece: a short-lived record of "one admin has
-- already approved this exact operation, waiting on a second, distinct one."
-- See services/api/src/mnemos_api/dual_control.py for the enforcement logic.
-- Deliberately NOT owned by mnemos_warden alone the way legal_holds and
-- residency_policies are — mnemos_warden.warden.Warden's own docstring is
-- explicit that dual control is enforced at the API layer, not inside the
-- Warden, so this table holds state for that layer even though only the
-- privileged connection may write it (below).

CREATE TABLE IF NOT EXISTS mnemos.pending_approvals (
    tenant_id              UUID        NOT NULL,
    approval_id            UUID        NOT NULL DEFAULT gen_random_uuid(),

    -- What was approved, and for what: e.g. operation='forget',
    -- target_key='patient:42', or operation='revoke_source',
    -- target_key='<sorted, comma-joined source_event_ids>'. A second call
    -- only consumes a pending row when both match exactly — approving a
    -- forget for subject A must never satisfy dual control for subject B.
    operation               STRING      NOT NULL,
    target_key              STRING      NOT NULL,
    reason                  STRING      NOT NULL,

    first_approver_key_id   UUID        NOT NULL,
    first_approver_label    STRING      NOT NULL,
    requested_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at              TIMESTAMPTZ NOT NULL,

    CONSTRAINT pk_pending_approvals PRIMARY KEY (tenant_id, approval_id),
    CONSTRAINT fk_pending_approvals_tenant FOREIGN KEY (tenant_id)
        REFERENCES mnemos.tenants (tenant_id)
);

-- The hot lookup: "is there already a live, unexpired approval for this
-- exact operation+target?" A stale expired row is left for a caller to
-- observe as absent rather than actively swept — cheap, and nothing here is
-- large enough to need a TTL job yet.
CREATE INDEX IF NOT EXISTS ix_pending_approvals_lookup
    ON mnemos.pending_approvals (tenant_id, operation, target_key, expires_at DESC);

-- Every other tenant-scoped table gets this (009_rls.sql); a pending
-- approval naming a subject_key or a reason string is exactly the kind of
-- row that must not be readable across a tenant boundary either.
ALTER TABLE mnemos.pending_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE mnemos.pending_approvals FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON mnemos.pending_approvals
    USING (tenant_id = mnemos.current_tenant())
    WITH CHECK (tenant_id = mnemos.current_tenant());

-- Only the Warden role may write here — consuming a pending approval DELETEs
-- it, and "no DELETE outside mnemos_warden" is invariant 1 applied literally,
-- even though this table holds governance bookkeeping rather than memory.
GRANT SELECT, INSERT, DELETE ON TABLE mnemos.pending_approvals TO mnemos_warden;
