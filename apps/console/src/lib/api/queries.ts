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
