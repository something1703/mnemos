-- 013 — fix: BEFORE DELETE trigger returned NULL and silently cancelled the row
--
-- require_audit ends with `RETURN NEW`. In a BEFORE DELETE trigger, NEW is NULL,
-- and a BEFORE-row trigger that returns NULL CANCELS the operation for that row.
--
-- So every DELETE was silently discarded: rowcount 0, no error, no rollback. The
-- Warden — the one component that is supposed to be able to destroy — could not
-- delete anything, and nothing said so. The `forget` flow in Phase 06 would have
-- reported success while leaving every row in place, and the erasure proof would
-- have been a lie of exactly the kind this project exists to prevent.
--
-- Caught by test_warden_can_delete, which asserts the *positive* half of
-- invariant 1. Testing only that the wrong roles are denied would have shipped
-- this: a control that blocks everyone looks identical to a control that works,
-- right up until someone needs it to permit something.
--
-- Fix: return OLD for DELETE, NEW otherwise. The audit-ticket check still runs
-- first, so deletions remain fully audited.

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

    -- NEW is NULL on DELETE; returning it would cancel the row silently.
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE PLPGSQL;
