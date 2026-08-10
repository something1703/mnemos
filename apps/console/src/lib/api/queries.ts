import "server-only";
import { apiGet } from "./server";
import type { LedgerOp, TrustState } from "@/lib/brand";

/**
 * Response shapes, written against the LIVE deployment rather than inferred.
 *
 * `schema.d.ts` is generated from the deployed `/openapi.json` and is the
 * authority on paths; FastAPI describes several of these bodies loosely (bare
 * `object`), so the field-level types below were read off real responses and
 * are narrowed here so the screens get real autocomplete instead of `any`.
 */

export interface Stats {
  episodes: number;
  facts_by_trust: Partial<Record<TrustState, number>>;
  chain_entries: number;
  active_holds: number;
  posture: {
    privilege_separation: boolean;
    privilege_separation_source: string;
    db_user: string;
    api_can_delete: boolean;
    warden_can_delete: boolean;
    model_provider: string;
    embed_model: string;
    embed_dimensions: number;
    distill_model: string;
    anchor_bucket_configured: boolean;
    chain_shards: number;
  };
}

export interface LedgerEntry {
  shard_id: number;
  seq: number;
  op: LedgerOp;
  actor: string;
  subject_key: string | null;
  reason: string | null;
  entry_hash: string;
  committed_at: string;
}

export interface LedgerPage {
  total: number;
  limit: number;
  offset: number;
  entries: LedgerEntry[];
}

export interface Checkpoint {
  checkpoint_seq: number;
  merkle_root: string;
  entry_count: number;
  covers_through: string;
  anchor_uri: string | null;
  anchored: boolean;
  anchored_at: string | null;
}

export interface VerifyResult {
  valid: boolean;
  entries_checked?: number;
  first_bad_shard?: number | null;
  first_bad_seq?: number | null;
  detail?: string | null;
}

export const getStats = () => apiGet<Stats>("/v1/stats", { revalidate: 10 });

export const getLedger = (limit = 40) =>
  apiGet<LedgerPage>(`/v1/ledger?limit=${limit}`, { revalidate: 4 });

export const getCheckpoints = () =>
  apiGet<{ checkpoints: Checkpoint[] }>("/v1/checkpoints", { revalidate: 10 });

export const verifyLedger = () =>
  apiGet<VerifyResult>("/v1/ledger/verify", { revalidate: 30 });

/* -------------------------------------------------- Custodian & governance */

export interface CustodianRun {
  run_id: string;
  trigger_source: "schedule" | "alarm" | "manual";
  trigger_detail: string | null;
  started_at: string;
  finished_at: string | null;
  status: "running" | "succeeded" | "failed" | "partial";
  skills_run: number;
  checks_run: number;
  checks_skipped: number;
  skipped_detail: Record<string, string[]> | null;
}

export interface CustodianFinding {
  finding_id: string;
  run_id: string;
  severity: "info" | "warn" | "critical";
  summary: string;
  evidence: Record<string, unknown> | null;
  recommendation: string | null;
  skill_id: string;
  tool_source: "mcp" | "ccloud";
  code: string;
  measured: boolean;
  fact_id: string | null;
  created_at: string;
}

export interface GovernanceProposal {
  proposal_id: string;
  proposed_by: string;
  kind: string;
  target: string;
  rationale: string;
  evidence: Record<string, unknown> | null;
  status: "pending" | "approved" | "rejected" | "executed";
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  created_at: string;
}

export interface Crossing {
  subject_key: string;
  from_region: string;
  to_region: string;
  projection: string | null;
  policy_applied: string | null;
  allowed: boolean;
  denied_reason: string | null;
  requested_by: string | null;
  occurred_at: string;
}

export interface Hold {
  hold_id: string;
  subject_key: string;
  matter_reference: string;
  placed_by: string;
  placed_at: string;
  released_at: string | null;
  active: boolean;
}

export interface Residency {
  subject_key: string;
  episode_regions: Record<string, number>;
  fact_regions: Record<string, number>;
  governing_policy: Record<string, unknown> | null;
}

export const getCustodianRuns = (limit = 20) =>
  apiGet<{ runs: CustodianRun[] }>(`/v1/custodian/runs?limit=${limit}`, { revalidate: 10 });

export const getCustodianFindings = (params: { runId?: string; limit?: number } = {}) => {
  const search = new URLSearchParams();
  if (params.runId) search.set("run_id", params.runId);
  search.set("limit", String(params.limit ?? 100));
  return apiGet<{ findings: CustodianFinding[] }>(`/v1/custodian/findings?${search}`, {
    revalidate: 10,
  });
};

export const getProposals = () =>
  apiGet<{ proposals: GovernanceProposal[] }>("/v1/governance/proposals", { revalidate: 10 });

export const getCrossings = (limit = 50) =>
  apiGet<{ crossings: Crossing[] }>(`/v1/crossings?limit=${limit}`, { revalidate: 10 });

export const getHolds = () =>
  apiGet<{ holds: Hold[] }>("/v1/holds?active_only=false", { revalidate: 10 });

export const getResidency = (subjectKey: string) =>
  apiGet<Residency>(`/v1/residency/${encodeURIComponent(subjectKey)}`, { revalidate: 10 });

export const getFacts = (params: { limit?: number; trust?: string } = {}) => {
  const search = new URLSearchParams();
  search.set("limit", String(params.limit ?? 100));
  if (params.trust) search.set("trust", params.trust);
  return apiGet<{
    total: number;
    facts: {
      fact_id: string;
      subject_key: string;
      fact_kind: string;
      trust: TrustState;
      home_region: string;
      created_at: string;
    }[];
  }>(`/v1/facts?${search}`, { revalidate: 10 });
};
