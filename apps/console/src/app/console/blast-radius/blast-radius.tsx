"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Radar } from "lucide-react";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
} from "@/components/ui/primitives";
import { TrustBadge } from "@/components/ui/trust-badge";
import { HashLink } from "@/components/ui/hash-link";
import { AdminKeyField, ConfirmGate, callAdmin } from "@/components/admin-gate";
import type { TrustState } from "@/lib/brand";

/** A real, live episode on the ops tenant — the seeded, deliberately poisoned
 * "postmortem" from db/seed.py (queried directly against the deployed
 * cluster, 2026-08-17): reads like remediation advice, carries an embedded
 * instruction, and is exactly what this screen exists to trace. Not fixture
 * data invented for this link — the actual Phase 10 red-team target. */
const EXAMPLE_TENANT = "ops";
const EXAMPLE_EVENT_ID = "65a7c0ba-1ff7-4ad5-a593-03fa15984aa6";

interface BlastResult {
  summary: string;
  counts: {
    facts: number;
    skills: number;
    recalls: number;
    actions: number;
    derived_episodes: number;
  };
  max_depth: number;
  truncated: boolean;
  facts: { fact_id: string; subject_key: string; trust: TrustState; depth: number }[];
  skills: { skill_id?: string; name?: string }[];
  action_ids: string[];
}

interface RevokeResult {
  quarantined_fact_ids?: string[];
  demoted_fact_ids?: string[];
  quarantined_skill_ids?: string[];
  contaminated_action_ids?: string[];
  [key: string]: unknown;
}

const HOPS = [
  { key: "facts", label: "Facts" },
  { key: "skills", label: "Skills" },
  { key: "recalls", label: "Recalls" },
  { key: "actions", label: "Actions" },
  { key: "derived_episodes", label: "Laundered episodes" },
] as const;

export function BlastRadius() {
  const router = useRouter();
  const [eventIds, setEventIds] = React.useState("");
  const [adminKey, setAdminKey] = React.useState("");
  const [reason, setReason] = React.useState("");
  const [blast, setBlast] = React.useState<BlastResult | null>(null);
  const [revoked, setRevoked] = React.useState<RevokeResult | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState(false);
  const [switching, setSwitching] = React.useState(false);

  async function tryExample() {
    setSwitching(true);
    try {
      // /api/blast-radius resolves the tenant key fresh on every request
      // (lib/api/mcp.ts), so the cookie POST alone is enough for the trace
      // itself. router.refresh() (not a full reload) re-renders the sidebar
      // tenant switcher to match, without losing this component's state.
      await fetch("/api/tenant", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ slug: EXAMPLE_TENANT }),
      });
      setEventIds(EXAMPLE_EVENT_ID);
      router.refresh();
    } finally {
      setSwitching(false);
    }
  }

  const ids = eventIds
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);

  async function preview() {
    setError(null);
    setRevoked(null);
    setPending(true);
    try {
      // The preview needs no admin key — seeing the damage should never be
      // gated behind the credential that causes it.
      const response = await fetch("/api/blast-radius", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_event_ids: ids }),
      });
      const body = await response.json();
      if (!body.ok) throw new Error(body.error ?? "preview failed");
      setBlast(body.result as BlastResult);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "preview failed");
    } finally {
      setPending(false);
    }
  }

  async function revoke() {
    setError(null);
    setPending(true);
    try {
      const result = (await callAdmin(adminKey, "revoke_source", {
        source_event_ids: ids,
        reason,
        confirm: true,
      })) as RevokeResult;
      setRevoked(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "revoke failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader>
          <CardTitle>Source episodes</CardTitle>
        </CardHeader>
        <CardBody className="flex flex-col gap-3">
          <textarea
            value={eventIds}
            onChange={(event) => setEventIds(event.target.value)}
            rows={2}
            placeholder="One or more event ids, separated by spaces or commas"
            aria-label="Source event ids"
            className="w-full rounded border border-hairline bg-veil px-3 py-2 font-mono text-sm text-parchment placeholder:font-body placeholder:text-dim"
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="primary"
              onClick={preview}
              disabled={ids.length === 0 || pending}
              className="w-fit"
            >
              <Radar className="size-4" aria-hidden />
              {pending && !revoked ? "Tracing…" : "Trace blast radius"}
            </Button>
            <button
              type="button"
              onClick={tryExample}
              disabled={switching}
              className="inline-flex items-center gap-1.5 text-left text-xs text-synapse hover:underline disabled:opacity-60"
            >
              {switching
                ? "Switching to the ops tenant…"
                : "Try the seeded example — a poisoned postmortem, real and live"}
              {!switching ? <ArrowRight className="size-3" aria-hidden /> : null}
            </button>
          </div>
        </CardBody>
      </Card>

      {error ? (
        <Card className="border-signal-edge bg-signal-fill">
          <CardBody className="py-3">
            <p className="text-sm font-medium text-signal">Refused</p>
            <p className="mt-1 font-mono text-xs break-all text-moonstone">{error}</p>
          </CardBody>
        </Card>
      ) : null}

      {blast ? (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Contamination graph</CardTitle>
              <span className="font-mono text-xs text-moonstone">{blast.summary}</span>
            </CardHeader>
            <CardBody className="flex flex-col gap-4">
              {/* Counts per hop, laid out as the actual path contamination
                  travels. The arrow between them is the point: each hop is a
                  place the poison stopped looking like poison. */}
              <ol className="flex flex-wrap items-stretch gap-2">
                {HOPS.map((hop, i) => {
                  const count = blast.counts[hop.key] ?? 0;
                  const touched = count > 0;
                  return (
                    <li key={hop.key} className="flex items-center gap-2">
                      <div
                        className={`min-w-[7.5rem] rounded-lg border px-3 py-2 transition-colors ${
                          revoked
                            ? "border-umbra-edge bg-umbra-fill"
                            : touched
                              ? "border-signal-edge bg-signal-fill"
                              : "border-hairline bg-veil-hi"
                        }`}
                      >
                        <p
                          className={`tabular font-display text-2xl ${
                            revoked ? "text-umbra" : touched ? "text-signal" : "text-dim"
                          }`}
                        >
                          {count}
                        </p>
                        <p className="text-xs text-moonstone">{hop.label}</p>
                      </div>
                      {i < HOPS.length - 1 ? (
                        <ArrowRight className="size-4 shrink-0 text-dim" aria-hidden />
                      ) : null}
                    </li>
                  );
                })}
              </ol>

              {blast.truncated ? (
                <p className="text-xs text-ember">
                  Truncated at depth {blast.max_depth} — the real radius is larger than what is
                  shown. Said out loud rather than silently capped.
                </p>
              ) : null}

              {blast.facts.length > 0 ? (
                <ul className="flex flex-col gap-2">
                  {blast.facts.map((fact) => (
                    <li
                      key={fact.fact_id}
                      className="flex flex-wrap items-center gap-2 rounded border border-hairline bg-veil-hi/40 px-3 py-2 text-sm"
                    >
                      <TrustBadge
                        trust={
                          revoked && revoked.quarantined_fact_ids?.includes(fact.fact_id)
                            ? "quarantined"
                            : fact.trust
                        }
                      />
                      <span className="font-mono text-xs text-parchment">{fact.subject_key}</span>
                      <span className="text-xs text-moonstone">depth {fact.depth}</span>
                      <HashLink value={fact.fact_id} className="ml-auto" head={6} tail={4} />
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-moonstone">
                  No facts were derived from this source — nothing downstream to revoke.
                </p>
              )}
            </CardBody>
          </Card>

          {revoked ? (
            <Card className="border-umbra-edge">
              <CardHeader>
                <CardTitle>Revoked</CardTitle>
                <span className="text-xs text-umbra">one transaction, nothing deleted</span>
              </CardHeader>
              <CardBody className="flex flex-col gap-2 text-sm">
                <Line
                  label="Quarantined"
                  value={revoked.quarantined_fact_ids?.length ?? 0}
                  hint="No support left once the revoked evidence is set aside"
                />
                <Line
                  label="Demoted, not quarantined"
                  value={revoked.demoted_fact_ids?.length ?? 0}
                  hint="Still corroborated by genuinely independent evidence — over-revocation is as much a bug as under-revocation"
                />
                <Line
                  label="Skills quarantined"
                  value={revoked.quarantined_skill_ids?.length ?? 0}
                  hint="A playbook resting on a quarantined fact is quarantined outright"
                />
                <Line
                  label="Actions marked contaminated"
                  value={revoked.contaminated_action_ids?.length ?? 0}
                  hint="explain() on these now reports the contamination"
                />
                <p className="mt-2 text-xs text-moonstone">
                  Nothing was deleted. A revocation says the evidence should not be trusted — not
                  that the record of what happened should vanish.
                </p>
              </CardBody>
            </Card>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>Revoke this source</CardTitle>
              </CardHeader>
              <CardBody className="flex flex-col gap-4">
                <AdminKeyField value={adminKey} onChange={setAdminKey} />
                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium tracking-wide text-moonstone uppercase">
                    Reason (recorded in the audit row)
                  </span>
                  <input
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="Why this source is no longer trustworthy"
                    className="h-10 w-full rounded border border-hairline bg-veil px-3 text-sm text-parchment placeholder:text-dim"
                  />
                </label>
                <ConfirmGate
                  phrase="REVOKE"
                  label="Revoke source"
                  pending={pending}
                  disabled={!adminKey || !reason}
                  onConfirm={revoke}
                >
                  <p className="text-sm text-moonstone">
                    {blast.counts.facts} fact(s), {blast.counts.skills} skill(s) and{" "}
                    {blast.counts.actions} action(s) will be re-evaluated in one transaction.
                  </p>
                </ConfirmGate>
              </CardBody>
            </Card>
          )}
        </>
      ) : (
        <EmptyState title="Trace a source before you touch it">
          Paste an episode id above. The graph is computed and shown in full{" "}
          <em>before</em> any revoke is possible — deciding what to destroy while looking at the
          consequences is the entire point.
        </EmptyState>
      )}
    </div>
  );
}

function Line({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <div className="flex flex-col gap-0.5 border-b border-hairline/50 pb-2 last:border-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-moonstone">{label}</span>
        <span className="tabular font-mono text-parchment">{value}</span>
      </div>
      <span className="text-xs text-moonstone">{hint}</span>
    </div>
  );
}
