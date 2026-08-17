import Link from "next/link";
import { getCheckpoints, getLedger, getStats, verifyLedger } from "@/lib/api/queries";
import { apiConfigured } from "@/lib/api/server";
import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  Stat,
} from "@/components/ui/primitives";
import { HashLink } from "@/components/ui/hash-link";
import { TrustBar } from "@/components/viz/trust-bar";
import { LedgerActivityChart } from "@/components/viz/ledger-activity-chart";
import { ActivitySparkline } from "@/components/viz/activity-sparkline";
import { CountUp } from "@/components/ui/count-up";
import { Reveal } from "@/components/ui/reveal";
import { bucketByHour, countByOp } from "@/lib/chart-data";
import { formatCount, relativeTime } from "@/lib/utils";
import type { TrustState } from "@/lib/brand";

export const revalidate = 15;

export default async function OverviewPage() {
  if (!apiConfigured()) return <NotConnected />;

  // 200 (the API's max page size) rather than a handful — the two charts
  // below need real volume to bucket, not just "the most recent entry".
  const [stats, ledger, checkpoints, verdict] = await Promise.all([
    getStats().catch(() => null),
    getLedger(200).catch(() => null),
    getCheckpoints().catch(() => null),
    verifyLedger().catch(() => null),
  ]);

  if (!stats) {
    return (
      <EmptyState title="The API did not answer">
        The console reached for <span className="font-mono">/v1/stats</span> and got nothing back.
        Check that the deployment is up and that{" "}
        <span className="font-mono">MNEMOS_API_KEY_READ</span> is a live key for this tenant.
      </EmptyState>
    );
  }

  const trusted =
    (stats.facts_by_trust.trusted ?? 0) + (stats.facts_by_trust.corroborated ?? 0);
  const unverified = stats.facts_by_trust.unverified ?? 0;
  const latestCheckpoint = checkpoints?.checkpoints?.[0] ?? null;
  const entries = ledger?.entries ?? [];
  const latestEntry = entries[0] ?? null;
  const opCounts = countByOp(entries);
  const hourlyBuckets = bucketByHour(entries, 24);

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-display text-3xl tracking-[-0.02em]">Overview</h1>
        <p className="max-w-prose text-sm text-moonstone">
          What this agent actually believes, how much of it has been earned, and whether the record
          of how it got there still verifies.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-3">
        <Reveal className="lg:col-span-1">
          <Card className="h-full">
            <CardBody className="flex h-full flex-col justify-between gap-4">
              <Stat
                label="Facts trusted"
                value={<CountUp value={trusted} />}
                size="hero"
                accent="synapse"
                hint="Deliberately not “facts stored”. The number that matters is the number you would act on."
              />
              <p className="text-xs text-moonstone">
                {formatCount(unverified)} more are written but unverified, and are withheld from
                recall until something independent corroborates them.
              </p>
            </CardBody>
          </Card>
        </Reveal>

        <Reveal delay={0.06} className="lg:col-span-2">
          <Card className="h-full">
            <CardHeader>
              <CardTitle>Trust distribution</CardTitle>
              <Link href="/console/explorer" className="text-xs text-synapse hover:underline">
                Explore facts →
              </Link>
            </CardHeader>
            <CardBody>
              <TrustBar counts={stats.facts_by_trust as Partial<Record<TrustState, number>>} />
            </CardBody>
          </Card>
        </Reveal>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {[
          {
            label: "Episodes",
            value: <CountUp value={stats.episodes} />,
            hint: "Raw experience recorded",
          },
          {
            label: "Chain entries",
            value: <CountUp value={stats.chain_entries} />,
            accent: "ember" as const,
            hint: `Across ${stats.posture.chain_shards} shards`,
          },
          {
            label: "Active legal holds",
            value: <CountUp value={stats.active_holds} />,
            accent: stats.active_holds > 0 ? ("ember" as const) : ("parchment" as const),
            hint: "Erasure is refused while a hold stands",
          },
          {
            label: "Last ledger commit",
            value: relativeTime(latestEntry?.committed_at),
            hint: latestEntry ? `${latestEntry.op} · shard ${latestEntry.shard_id}` : undefined,
          },
        ].map((s, i) => (
          <Reveal key={s.label} delay={i * 0.05}>
            <Card>
              <CardBody>
                <Stat label={s.label} value={s.value} accent={s.accent} hint={s.hint} />
              </CardBody>
            </Card>
          </Reveal>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Reveal>
          <Card className="h-full">
            <CardHeader>
              <CardTitle>Ledger activity</CardTitle>
              <span className="text-xs text-dim">last {formatCount(entries.length)} entries</span>
            </CardHeader>
            <CardBody>
              <LedgerActivityChart counts={opCounts} />
            </CardBody>
          </Card>
        </Reveal>

        <Reveal delay={0.06}>
          <Card className="h-full">
            <CardHeader>
              <CardTitle>Commits, last 24h</CardTitle>
              <Link href="/console/ledger" className="text-xs text-synapse hover:underline">
                Open ledger →
              </Link>
            </CardHeader>
            <CardBody>
              <ActivitySparkline buckets={hourlyBuckets} />
            </CardBody>
          </Card>
        </Reveal>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Reveal>
          <Card className="h-full">
            <CardHeader>
              <CardTitle>Chain integrity</CardTitle>
              <Link href="/console/ledger" className="text-xs text-synapse hover:underline">
                Open ledger →
              </Link>
            </CardHeader>
            <CardBody className="flex flex-col gap-4">
              {/* Two badges, not one — a single tick here would be exactly
                  the collapsed distinction the Ledger page's own module
                  docstring warns against: internal consistency and external
                  anchoring are different verdicts with different attackers
                  they defend against. */}
              <div className="flex flex-wrap items-center gap-3">
                <span
                  className={
                    verdict === undefined || verdict === null
                      ? "rounded-sm border border-hairline bg-veil-hi px-2 py-0.5 text-xs font-semibold text-moonstone"
                      : verdict.valid
                        ? "rounded-sm border border-synapse-edge bg-synapse-fill px-2 py-0.5 text-xs font-semibold text-synapse"
                        : "rounded-sm border border-signal-edge bg-signal-fill px-2 py-0.5 text-xs font-semibold text-signal"
                  }
                >
                  {verdict ? (verdict.valid ? "VALID" : "BROKEN") : "UNKNOWN"}
                </span>
                <span
                  className={
                    latestCheckpoint?.anchored
                      ? "rounded-sm border border-synapse-edge bg-synapse-fill px-2 py-0.5 text-xs font-semibold text-synapse"
                      : "rounded-sm border border-ember-edge bg-ember-fill px-2 py-0.5 text-xs font-semibold text-ember"
                  }
                >
                  {latestCheckpoint?.anchored ? "ANCHORED" : "NOT ANCHORED"}
                </span>
                <span className="text-sm text-moonstone">
                  {verdict
                    ? `Recomputed over ${formatCount(verdict.entries_checked ?? stats.chain_entries)} entries just now`
                    : "The verifier did not answer"}
                </span>
              </div>

              {latestCheckpoint ? (
                <dl className="flex flex-col gap-2 text-sm">
                  <Row label="Latest checkpoint">
                    <span className="tabular font-mono text-parchment">
                      #{latestCheckpoint.checkpoint_seq}
                    </span>
                  </Row>
                  <Row label="Merkle root">
                    <HashLink value={latestCheckpoint.merkle_root} />
                  </Row>
                  <Row label="Anchored to S3">
                    {latestCheckpoint.anchored ? (
                      <span className="text-synapse">
                        yes · {relativeTime(latestCheckpoint.anchored_at)}
                      </span>
                    ) : (
                      <span className="text-ember">
                        not yet — anchoring is still a manual step
                      </span>
                    )}
                  </Row>
                </dl>
              ) : (
                <p className="text-sm text-moonstone">No checkpoints yet.</p>
              )}
            </CardBody>
          </Card>
        </Reveal>

        <Reveal delay={0.06}>
          <Card className="h-full">
            <CardHeader>
              <CardTitle>Posture</CardTitle>
              <span className="text-xs text-moonstone">measured, not configured</span>
            </CardHeader>
            <CardBody>
              <dl className="flex flex-col gap-2 text-sm">
                <Row label="API database role">
                  <span className="font-mono text-parchment">{stats.posture.db_user}</span>
                </Row>
                <Row label="API can DELETE">
                  <span className={stats.posture.api_can_delete ? "text-signal" : "text-synapse"}>
                    {stats.posture.api_can_delete ? "yes — invariant 1 is not held" : "no"}
                  </span>
                </Row>
                <Row label="Privilege separation">
                  <span className={stats.posture.privilege_separation ? "text-synapse" : "text-signal"}>
                    {String(stats.posture.privilege_separation)} ({stats.posture.privilege_separation_source})
                  </span>
                </Row>
                <Row label="Distillation model">
                  <span className="font-mono text-parchment">{stats.posture.distill_model}</span>
                </Row>
              </dl>
            </CardBody>
          </Card>
        </Reveal>
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-moonstone">{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}

function NotConnected() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="font-display text-3xl tracking-[-0.02em]">Overview</h1>
      <EmptyState title="Not connected to a Mnemos deployment">
        Set <span className="font-mono">MNEMOS_API_URL</span> and{" "}
        <span className="font-mono">MNEMOS_API_KEY_READ</span> in{" "}
        <span className="font-mono">apps/console/.env.local</span>, then reload. Mint a read key
        with <span className="font-mono">mnemos-api mint-key --scope read</span>.
      </EmptyState>
    </div>
  );
}
