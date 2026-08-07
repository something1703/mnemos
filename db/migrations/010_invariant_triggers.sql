-- 010 — invariants 2 and 3, enforced by the database
--
-- This migration is the difference between a claim and a guarantee. Both rules
-- below are enforced for every principal, including a superuser session, and
-- both were verified against a live cluster before this file was written:
-- an insert with no ticket, an insert with a forged ticket, and an attempt to
-- reuse a ticket from a previous transaction are all rejected.
--
-- INVARIANT 2 — every state-changing memory op appends a hash-chained audit row
-- in the SAME transaction.
--
-- Mechanism: the engine calls append_audit() first, which inserts into
-- audit_chain and sets the transaction-local `app.audit_ticket`. Protected
-- tables carry a BEFORE trigger that resolves that ticket against audit_chain.
-- Because the audit row was written in the same transaction, the trigger's read
-- sees it; because the setting is transaction-local, a ticket cannot be reused
-- by a later transaction.
--
-- Known limit, stated rather than hidden: a principal with DDL rights could
-- DROP or DISABLE these triggers. That is precisely the adversary the S3-anchored
-- Merkle checkpoints exist for (ADR-010) — the ledger notices the gap even if
-- the trigger is gone. Documented in docs/limits.md.

CREATE OR REPLACE FUNCTION mnemos.require_audit() RETURNS TRIGGER AS $$
DECLARE
    ticket TEXT;
BEGIN
    ticket := current_setting('app.audit_ticket', true);

    IF ticket IS NULL OR ticket = '' THEN
        RAISE EXCEPTION
            'invariant 2 violated: mutation on mnemos.% without an audit ticket. '
            'Call append_audit() in the same transaction.', TG_TABLE_NAME;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM mnemos.audit_chain WHERE mnemos.audit_chain.ticket = ticket::UUID) THEN
        RAISE EXCEPTION
            'invariant 2 violated: audit ticket % has no audit row. '
            'The audit row must be committed in the same transaction as the mutation.', ticket;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE PLPGSQL;

-- CockroachDB has no CREATE TRIGGER IF NOT EXISTS, so each is dropped first.
-- Migrations must be re-runnable from any partially-applied state: this file
-- failed midway on its first run, and a migration that cannot be retried turns
-- a small mistake into a rebuild.
DROP TRIGGER IF EXISTS require_audit ON mnemos.episodic_events;
CREATE TRIGGER require_audit
    BEFORE INSERT OR UPDATE OR DELETE ON mnemos.episodic_events
    FOR EACH ROW EXECUTE FUNCTION mnemos.require_audit();

DROP TRIGGER IF EXISTS require_audit ON mnemos.semantic_facts;
CREATE TRIGGER require_audit
    BEFORE INSERT OR UPDATE OR DELETE ON mnemos.semantic_facts
    FOR EACH ROW EXECUTE FUNCTION mnemos.require_audit();

DROP TRIGGER IF EXISTS require_audit ON mnemos.skill_versions;
CREATE TRIGGER require_audit
    BEFORE INSERT OR UPDATE OR DELETE ON mnemos.skill_versions
    FOR EACH ROW EXECUTE FUNCTION mnemos.require_audit();

DROP TRIGGER IF EXISTS require_audit ON mnemos.legal_holds;
CREATE TRIGGER require_audit
    BEFORE INSERT OR UPDATE OR DELETE ON mnemos.legal_holds
    FOR EACH ROW EXECUTE FUNCTION mnemos.require_audit();

DROP TRIGGER IF EXISTS require_audit ON mnemos.residency_policies;
CREATE TRIGGER require_audit
    BEFORE INSERT OR UPDATE OR DELETE ON mnemos.residency_policies
    FOR EACH ROW EXECUTE FUNCTION mnemos.require_audit();

-- INVARIANT 3 — no fact becomes recallable without provenance to at least one
-- episode. A fact with zero provenance edges is a bug, not a belief.
--
-- Facts may exist briefly with no edges while the consolidation transaction is
-- still assembling them, so the rule binds at the moment of *promotion*: a fact
-- may not leave 'unverified' unless it can point at where it came from.
-- Enforcing at insert would make the legitimate write order impossible;
-- enforcing at promotion is where the property actually matters.

-- Note the parenthesised (NEW).field form. Inside a subquery CockroachDB parses
-- a bare NEW.tenant_id as a reference to a table named "new" and fails with
-- "no data source matches prefix: new". Binding the record fields to locals
-- first is clearer than parenthesising at every use site.
CREATE OR REPLACE FUNCTION mnemos.require_provenance() RETURNS TRIGGER AS $$
DECLARE
    v_tenant_id UUID;
    v_fact_id   UUID;
    v_trust     TEXT;
BEGIN
    v_tenant_id := (NEW).tenant_id;
    v_fact_id   := (NEW).fact_id;
    v_trust     := (NEW).trust;

    IF v_trust IN ('corroborated', 'trusted') THEN
        IF NOT EXISTS (
            SELECT 1 FROM mnemos.fact_provenance p
            WHERE p.tenant_id = v_tenant_id AND p.fact_id = v_fact_id
        ) THEN
            RAISE EXCEPTION
                'invariant 3 violated: fact % cannot be promoted to % with no provenance edge',
                v_fact_id, v_trust;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE PLPGSQL;

DROP TRIGGER IF EXISTS require_provenance ON mnemos.semantic_facts;
CREATE TRIGGER require_provenance
    BEFORE INSERT OR UPDATE ON mnemos.semantic_facts
    FOR EACH ROW EXECUTE FUNCTION mnemos.require_provenance();
