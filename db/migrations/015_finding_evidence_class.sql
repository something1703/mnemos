-- 015 — distinguish a measured finding from an interpreted one.
--
-- PHASE_07 7.3 promised that a Custodian finding is "promoted only when a
-- second independent sweep or a metric corroborates" it. Running the real
-- sweep four times against the live cluster showed that first clause cannot
-- work and should not: `max_independent_corroborations` matches sessions
-- against source-trust categories, so N sweeps all writing `source_trust=
-- 'agent'` can only ever fill the single `agent` slot. The count is pinned
-- at 1 forever. That is the anti-poisoning primitive behaving exactly as
-- docs/trust.md specifies — an agent must never be able to promote its own
-- claims by repeating them.
--
-- The second clause is the real mechanism. Some of what the Custodian
-- produces is not the model's opinion at all: `check_backup_recency` is a
-- pure function over the Cloud REST API's own response, and cluster state is
-- a field read. Those are measurements. They enter memory as `external`
-- (third-party system output, untrusted on arrival, and crucially a
-- DIFFERENT source-trust category from `agent`), which lets a measurement in
-- one sweep and an interpretation in another corroborate each other
-- honestly.
--
-- `measured` records which of the two a finding was, because the difference
-- is exactly what justifies its provenance label and the console has to be
-- able to show it. Defaults false: every existing row was model-interpreted.

ALTER TABLE mnemos.custodian_findings
    ADD COLUMN IF NOT EXISTS measured BOOL NOT NULL DEFAULT false;
