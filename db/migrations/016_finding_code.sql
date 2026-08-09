-- 016 — a stable identity for a recurring Custodian finding.
--
-- Measured, not assumed: two live sweeps observing the same cluster state
-- produced "Cluster is not in the RUNNING state" and "Basic cluster is not in
-- the RUNNING state" — 0.9029 cosine similarity, under the 0.92 reinforce
-- threshold, so they became two separate unverified facts instead of one
-- corroborated one. Longer distiller paraphrases of the same observation
-- scored 0.66-0.88. Free-text phrasing does not reliably reinforce.
--
-- `code` lets semantically identical observations produce byte-identical
-- claim text (mnemos_custodian.findings.FindingCode), so corroboration keys
-- on the condition rather than on the model's word choice. 'other' is the
-- open-vocabulary escape hatch and stays the default for existing rows.

ALTER TABLE mnemos.custodian_findings
    ADD COLUMN IF NOT EXISTS code STRING NOT NULL DEFAULT 'other';

CREATE INDEX IF NOT EXISTS ix_findings_code
    ON mnemos.custodian_findings (tenant_id, code, created_at DESC);
