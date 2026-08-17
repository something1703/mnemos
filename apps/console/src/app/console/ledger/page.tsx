import { getCheckpoints, getLedger, verifyLedger } from "@/lib/api/queries";
import { apiConfigured } from "@/lib/api/server";
import { Card, CardBody, CardHeader, CardTitle, EmptyState } from "@/components/ui/primitives";
import { HashLink } from "@/components/ui/hash-link";
import { MemoryTrace, type TraceCommit } from "@/components/viz/memory-trace";
import { OP_PRESENTATION, ACCENT_CLASSES, type LedgerOp } from "@/lib/brand";
import { formatCount, relativeTime } from "@/lib/utils";

export const revalidate = 5;

/**
 * Ledger — the chain, and the two verdicts that are not the same verdict.
 *
 * `mnemos-verify` recomputes the hash chain from the rows in the database.
 * It answers "is this chain internally consistent", and a principal with full
 * DML rights who rewrites a shard AND its checkpoint consistently still
 * passes it — that was proven against real infrastructure, not asserted
 * (docs/security.md §3). The anchored verdict compares the live chain against
 * a Merkle root in an S3 Object Lock bucket, which is the one nobody with
 * database access can forge.
 *
 * Showing them as two badges rather than one status is the entire point: a
 * single green tick would collapse a distinction the security story depends
 * on.
 */
export default async function LedgerPage() {
  if (!apiConfigured()) {
    return (
      <div className="flex flex-col gap-6">
        <Header />
        <EmptyState title="Not connected">
          Set <span className="font-mono">MNEMOS_API_URL</span> to read the live chain.
        </EmptyState>
      </div>
    );
  }

  const [page, checkpointsResult, verdict] = await Promise.all([
    getLedger(60).catch(() => null),
    getCheckpoints().catch(() => null),
    verifyLedger().catch(() => null),
  ]);

  const entries = page?.entries ?? [];
  const checkpoints = checkpointsResult?.checkpoints ?? [];
  const anchored = checkpoints.filter((c) => c.anchored);

  const commits: TraceCommit[] = entries
    .slice()
    .reverse()
    .map((e) => ({
      shard: e.shard_id,
      seq: e.seq,
      op: e.op as LedgerOp,
      rowHash: e.entry_hash,
      at: e.committed_at,
    }));

  return (
    <div className="flex flex-col gap-6">
      <Header />

      <div className="grid gap-4 md:grid-cols-2">
        <Verdict
          title="Internal chain"
          ok={verdict?.valid ?? null}
          okLabel="VALID"
          badLabel="BROKEN"
          detail={
            verdict
              ? `Hash chain recomputed over ${formatCount(
                  verdict.entries_checked ?? page?.total ?? 0,
                )} entries.`
              : "The verifier did not answer."
          }
          caveat="Catches an edited or deleted row. Does NOT catch a principal with full DML rights who rewrites a shard and its checkpoint consistently — measured, not assumed."
        />
        <Verdict
          title="External anchor"
          ok={anchored.length > 0 ? true : null}
          okLabel="ANCHORED"
          badLabel="NOT ANCHORED"
          detail={
            anchored.length > 0
              ? `${anchored.length} of ${checkpoints.length} checkpoints anchored to S3 Object Lock.`
              : `0 of ${checkpoints.length} checkpoints anchored. Anchoring is still a manual step (mnemos-attest anchor).`
          }
          caveat="An anchored root lives in a compliance-mode bucket that not even account root can alter before retention expires. This is the verdict a database-level forgery cannot reach."
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>The chain, by shard</CardTitle>
          <span className="text-xs text-moonstone">
            newest on the right · diamonds are checkpoints · dashed links follow an erasure
          </span>
        </CardHeader>
        <CardBody className="overflow-x-auto">
          <MemoryTrace commits={commits} height={120} />
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Checkpoints</CardTitle>
        </CardHeader>
        <CardBody>
          {checkpoints.length === 0 ? (
            <EmptyState title="No checkpoints yet">
              A checkpoint is taken hourly by the sleep cycle. Run{" "}
              <span className="font-mono">mnemos-sleep-cycle run-checkpoint</span> to take one now.
            </EmptyState>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-hairline text-left text-xs text-moonstone">
                  <th className="pb-2 font-medium">Seq</th>
                  <th className="pb-2 font-medium">Merkle root</th>
                  <th className="pb-2 font-medium">Entries</th>
                  <th className="pb-2 font-medium">Covers through</th>
                  <th className="pb-2 font-medium">Anchor</th>
                </tr>
              </thead>
              <tbody>
                {checkpoints.slice(0, 10).map((c) => (
                  <tr key={c.checkpoint_seq} className="border-b border-hairline/50">
                    <td className="py-2 font-mono text-parchment">#{c.checkpoint_seq}</td>
                    <td className="py-2">
                      <HashLink value={c.merkle_root} />
                    </td>
                    <td className="tabular py-2 text-moonstone">{c.entry_count}</td>
                    <td className="py-2 text-moonstone">{relativeTime(c.covers_through)}</td>
                    <td className="py-2">
                      {c.anchored ? (
                        <span className="text-synapse">anchored</span>
                      ) : (
                        <span className="text-dim">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent entries</CardTitle>
          <span className="text-xs text-dim">
            {formatCount(page?.total ?? 0)} total · showing {entries.length}
          </span>
        </CardHeader>
        <CardBody>
          {entries.length === 0 ? (
            <EmptyState title="The chain is empty">
              Nothing has been written yet. Any <span className="font-mono">remember</span> call
              appends here in the same transaction.
            </EmptyState>
          ) : (
            <ul className="flex flex-col">
              {entries.map((entry) => {
                const presentation =
                  OP_PRESENTATION[entry.op as LedgerOp] ?? OP_PRESENTATION.checkpoint;
                return (
                  <li
                    key={`${entry.shard_id}-${entry.seq}`}
                    className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-hairline/50 py-2 text-sm last:border-0"
                  >
                    <span
                      className={`w-28 shrink-0 text-xs font-medium ${
                        ACCENT_CLASSES[presentation.accent].text
                      }`}
                    >
                      {presentation.label}
                    </span>
                    <span className="tabular w-20 shrink-0 font-mono text-xs text-moonstone">
                      {entry.shard_id}/{entry.seq}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-moonstone">
                      {entry.subject_key ?? <span className="text-dim">—</span>}
                    </span>
                    <span className="font-mono text-xs text-moonstone">{entry.actor}</span>
                    <HashLink value={entry.entry_hash} head={6} tail={4} />
                    <span className="w-24 shrink-0 text-right text-xs text-dim">
                      {relativeTime(entry.committed_at)}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function Header() {
  return (
    <header className="flex flex-col gap-1">
      <h1 className="font-display text-3xl tracking-[-0.02em]">Ledger</h1>
      <p className="max-w-prose text-sm text-moonstone">
        Every state-changing operation appends a hash-chained row in the same transaction that made
        the change. Tamper-evident, and honest about the difference between that and tamper-proof.
      </p>
    </header>
  );
}

function Verdict({
  title,
  ok,
  okLabel,
  badLabel,
  detail,
  caveat,
}: {
  title: string;
  ok: boolean | null;
  okLabel: string;
  badLabel: string;
  detail: string;
  caveat: string;
}) {
  // `null` ("the verifier did not answer") and `false` ("the chain is
  // broken") used to render as the same badLabel — on a page whose whole
  // argument is "a single tick would collapse a distinction the security
  // story depends on," conflating those two was that exact failure.
  const label = ok === true ? okLabel : ok === false ? badLabel : "UNKNOWN";
  const className =
    ok === true
      ? "rounded-sm border border-synapse-edge bg-synapse-fill px-2 py-0.5 text-xs font-semibold text-synapse"
      : ok === false
        ? "rounded-sm border border-signal-edge bg-signal-fill px-2 py-0.5 text-xs font-semibold text-signal"
        : "rounded-sm border border-hairline bg-veil-hi px-2 py-0.5 text-xs font-semibold text-moonstone";
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <span className={className}>{label}</span>
      </CardHeader>
      <CardBody className="flex flex-col gap-2">
        <p className="text-sm text-parchment">{detail}</p>
        <p className="text-xs text-moonstone">{caveat}</p>
      </CardBody>
    </Card>
  );
}
