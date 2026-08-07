-- 012 — fix: PL/pgSQL variable shadowed the column it was compared against
--
-- Migration 010 declared the local variable as `ticket`, which is also the name
-- of the audit_chain column it is compared to:
--
--     DECLARE ticket TEXT;
--     ...
--     WHERE mnemos.audit_chain.ticket = ticket::UUID
--
-- PL/pgSQL resolved the right-hand `ticket` to the COLUMN, not the variable, so
-- the predicate degenerated to `ticket = ticket` — true for every row. The
-- EXISTS check therefore succeeded whenever audit_chain contained any visible
-- row at all, and an arbitrary forged UUID satisfied the trigger.
--
-- Invariant 2 still rejected a *missing* ticket, so the control looked like it
-- worked. Caught by test_forged_ticket_is_rejected, which exists precisely
-- because "well-formed but meaningless" is the interesting case.
--
-- Fix: v_ prefix on every local, matching require_provenance. The convention is
-- not cosmetic — in PL/pgSQL, an unprefixed local that collides with a column
-- name is a silent correctness bug, not a syntax error.
--
-- This is a new migration rather than an edit to 010 because applied migrations
-- are immutable; the runner refuses to re-apply a file whose checksum changed.

CREATE OR REPLACE FUNCTION mnemos.require_audit() RETURNS TRIGGER AS $$
DECLARE
    v_ticket_text TEXT;
    v_ticket      UUID;
    v_found       BOOL;
BEGIN
    v_ticket_text := current_setting('app.audit_ticket', true);

    IF v_ticket_text IS NULL OR v_ticket_text = '' THEN
        RAISE EXCEPTION
            'invariant 2 violated: mutation on mnemos.% without an audit ticket. '
            'Call append_audit() in the same transaction.', TG_TABLE_NAME;
    END IF;

    v_ticket := v_ticket_text::UUID;

    SELECT EXISTS (
        SELECT 1 FROM mnemos.audit_chain AS ac WHERE ac.ticket = v_ticket
    ) INTO v_found;

    IF NOT v_found THEN
        RAISE EXCEPTION
            'invariant 2 violated: audit ticket % has no audit row. '
            'The audit row must be committed in the same transaction as the mutation.',
            v_ticket_text;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE PLPGSQL;
