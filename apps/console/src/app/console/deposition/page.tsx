import { apiGet, apiConfigured } from "@/lib/api/server";
import { Card, CardBody, CardHeader, CardTitle, EmptyState } from "@/components/ui/primitives";
import { HashLink } from "@/components/ui/hash-link";
import { TrustBadge } from "@/components/ui/trust-badge";
import { ActionForm } from "./action-form";
import { TryExampleLink } from "@/components/try-example-link";
import { relativeTime } from "@/lib/utils";
import { OP_PRESENTATION, ACCENT_CLASSES, type LedgerOp, type TrustState } from "@/lib/brand";

/** A real, live action_id on the ops tenant (queried directly against the
 * deployed cluster, 2026-08-17) — not seeded fixture data invented for this
 * link. The Deposition and Blast Radius pillars both had no way to discover
 * a valid id on their own; this is the fix, matching the worked example
 * Explorer already had (`?q=is+the+database+cluster+healthy%3F`). */
const EXAMPLE_ACTION_ID = "224c51a4-35b5-48b7-9be7-2496780199c4";

export const dynamic = "force-dynamic";

interface DepositionFact {
  fact_id: string;
  subject_key: string;
  trust_at_recall: TrustState;
  trust_now: TrustState;
  changed_since: boolean;
  revoked_since: boolean;
  superseded_since: boolean;
  score_at_recall: number;
  provenance: { event_id: string; source_trust?: string; occurred_at?: string }[];
}

interface Deposition {
  action_id: string;
  action_type: string;
  description: string;
  declared_at: string;
  contaminated: boolean;
  contamination_note: string | null;
  checkpoint_seq: number | null;
  merkle_root: string | null;
  anchor_uri: string | null;
  anchored: boolean;
  facts: DepositionFact[];
  audit_trail: {
    shard_id: number;
    seq: number;
    op: string;
    actor: string;
    entry_hash: string;
    committed_at: string;
  }[];
  summary?: string | null;
}

/**
 * Deposition — the causal chain behind one action, rendered as a document.
 *
 * The column that matters most is `trust_at_recall` versus `trust_now`. An
 * after-the-fact audit that only shows what a fact is believed to be TODAY
 * cannot answer whether the agent was reasonable at the time — and "the fact
 * it acted on has since been revoked" is precisely the finding an
 * investigation exists to surface. Showing both, and flagging the delta, is
 * the difference between a log and evidence.
 */
export default async function DepositionPage({
  searchParams,
}: {
  searchParams: Promise<{ action?: string }>;
}) {
  const { action } = await searchParams;

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-display text-3xl tracking-[-0.02em]">Deposition</h1>
        <p className="max-w-prose text-sm text-moonstone">
          What the agent did, which recalls caused it, what those facts said{" "}
          <em>at that moment</em>, their provenance back to raw episodes, and whether the record
          still verifies.
        </p>
      </header>

      <ActionForm actionId={action ?? ""} />

      {!apiConfigured() ? (
        <EmptyState title="Not connected">
          Set <span className="font-mono">MNEMOS_API_URL</span> to build a deposition.
        </EmptyState>
      ) : action ? (
        <Body actionId={action} />
      ) : (
        <EmptyState
          title="Enter an action id"
          action={
            <TryExampleLink tenantSlug="ops" href={`/console/deposition?action=${EXAMPLE_ACTION_ID}`}>
              Try a real one — an ops escalation, deposed live
            </TryExampleLink>
          }
        >
          Every <span className="font-mono">record_action</span> call an agent makes can be
          deposed. Paste its id above to reconstruct the chain that produced it.
        </EmptyState>
      )}
    </div>
  );
}

async function Body({ actionId }: { actionId: string }) {
  let deposition: Deposition;
  try {
    deposition = await apiGet<Deposition>(`/v1/deposition/${encodeURIComponent(actionId)}`, {
      revalidate: false,
    });
  } catch (cause) {
    return (
      <EmptyState title="No deposition for that id">
        {cause instanceof Error ? cause.message : "The API did not answer."}
      </EmptyState>
    );
  }

  const changed = deposition.facts.filter((f) => f.changed_since);

  return (
    <div className="flex flex-col gap-4">
      <Card className={deposition.contaminated ? "border-signal-edge" : undefined}>
        <CardHeader>
          <CardTitle>{deposition.action_type}</CardTitle>
          <a
            href={`/api/deposition/${encodeURIComponent(actionId)}/export`}
            className="rounded border border-synapse-edge bg-synapse-fill px-3 py-1.5 text-xs font-medium text-synapse hover:bg-synapse/20"
          >
            Export
          </a>
        </CardHeader>
        <CardBody className="flex flex-col gap-3">
          <p className="text-sm text-parchment">{deposition.description}</p>
          <dl className="grid gap-2 text-sm sm:grid-cols-2">
            <Row label="Declared">{relativeTime(deposition.declared_at)}</Row>
            <Row label="Action id">
              <HashLink value={deposition.action_id} />
            </Row>
            <Row label="Contaminated">
              {deposition.contaminated ? (
                <span className="text-signal">
                  yes — {deposition.contamination_note ?? "a source was revoked"}
                </span>
              ) : (
                <span className="text-synapse">no</span>
              )}
            </Row>
            <Row label="Anchor">
              {deposition.anchored ? (
                <span className="text-synapse">verified against S3</span>
              ) : (
                <span className="text-ember">not anchored</span>
              )}
            </Row>
          </dl>
        </CardBody>
      </Card>

      {changed.length > 0 ? (
        <Card className="border-ember-edge bg-ember-fill">
          <CardBody className="py-3">
            <p className="text-sm font-medium text-ember">
              {changed.length} of the {deposition.facts.length} facts behind this action have
              changed trust since it was taken.
            </p>
            <p className="mt-1 text-xs text-moonstone">
              That does not make the action wrong — it makes it explainable. The agent acted on
              what memory believed at the time.
            </p>
          </CardBody>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Facts it relied on</CardTitle>
          <span className="text-xs text-moonstone">then → now</span>
        </CardHeader>
        <CardBody>
          {deposition.facts.length === 0 ? (
            <EmptyState title="No recalls cited">
              This action declared no <span className="font-mono">recall_ids</span>, so nothing
              links it back to memory. An action that cites nothing cannot be deposed — which is
              itself worth knowing.
            </EmptyState>
          ) : (
            <ul className="flex flex-col gap-3">
              {deposition.facts.map((fact) => (
                <li key={fact.fact_id} className="rounded border border-hairline bg-veil-hi/40 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <TrustBadge trust={fact.trust_at_recall} />
                    <span className="text-dim">→</span>
                    <TrustBadge trust={fact.trust_now} />
                    {fact.revoked_since ? (
                      <span className="rounded-sm border border-signal-edge bg-signal-fill px-2 py-0.5 text-xs text-signal">
                        revoked since
                      </span>
                    ) : null}
                    {fact.superseded_since ? (
                      <span className="rounded-sm border border-ember-edge bg-ember-fill px-2 py-0.5 text-xs text-ember">
                        superseded since
                      </span>
                    ) : null}
                    <span className="ml-auto font-mono text-xs text-moonstone">
                      score {fact.score_at_recall?.toFixed?.(3) ?? "—"}
                    </span>
                  </div>
                  <p className="mt-2 font-mono text-xs text-parchment">{fact.subject_key}</p>
                  <p className="mt-1 text-xs text-moonstone">
                    {fact.provenance.length} provenance edge
                    {fact.provenance.length === 1 ? "" : "s"} back to raw episodes
                  </p>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Audit trail</CardTitle>
          <span className="text-xs text-dim">{deposition.audit_trail.length} chained entries</span>
        </CardHeader>
        <CardBody className="max-h-96 overflow-y-auto">
          <ul className="flex flex-col">
            {deposition.audit_trail.map((entry) => {
              const presentation =
                OP_PRESENTATION[entry.op as LedgerOp] ?? OP_PRESENTATION.checkpoint;
              return (
                <li
                  key={`${entry.shard_id}-${entry.seq}`}
                  className="flex flex-wrap items-center gap-x-3 border-b border-hairline/40 py-1.5 text-xs last:border-0"
                >
                  <span
                    className={`w-24 shrink-0 font-medium ${
                      ACCENT_CLASSES[presentation.accent].text
                    }`}
                  >
                    {presentation.label}
                  </span>
                  <span className="tabular w-16 shrink-0 font-mono text-moonstone">
                    {entry.shard_id}/{entry.seq}
                  </span>
                  <span className="flex-1 truncate font-mono text-moonstone">{entry.actor}</span>
                  <HashLink value={entry.entry_hash} head={6} tail={4} />
                </li>
              );
            })}
          </ul>
        </CardBody>
      </Card>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-moonstone">{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}
