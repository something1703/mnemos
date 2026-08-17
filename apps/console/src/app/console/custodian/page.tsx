import Link from "next/link";
import { AlertTriangle, Bot, CalendarClock, Hand, Ruler } from "lucide-react";
import { getCustodianFindings, getCustodianRuns, getProposals } from "@/lib/api/queries";
import { apiConfigured } from "@/lib/api/server";
import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  SeverityBadge,
  Stat,
} from "@/components/ui/primitives";
import { HashLink } from "@/components/ui/hash-link";
import { formatCount, relativeTime } from "@/lib/utils";
import type { CustodianFinding, CustodianRun } from "@/lib/api/queries";

export const revalidate = 10;

const TRIGGER_ICON = {
  schedule: CalendarClock,
  alarm: AlertTriangle,
  manual: Hand,
} as const;

/**
 * Custodian — the agent that maintains the database, and is subject to the
 * same trust rules as any other agent.
 *
 * Two things get first-class treatment because they are the point:
 *
 * `skill_id` is shown on every finding. These are CockroachDB's own published
 * Agent Skills, vendored unmodified, and a judge should be able to see the
 * sponsor's own work doing the diagnosis.
 *
 * `measured` vs interpreted is shown as a distinct badge rather than folded
 * into `tool_source`. A deterministic reading of the control plane and the
 * model's opinion about a query result are different KINDS of evidence — that
 * difference is exactly what lets two sweeps corroborate each other, and
 * hiding it would make the corroboration look like an agent believing itself.
 */
export default async function CustodianPage({
  searchParams,
}: {
  searchParams: Promise<{ run?: string }>;
}) {
  const { run: selectedRun } = await searchParams;

  if (!apiConfigured()) {
    return (
      <div className="flex flex-col gap-6">
        <Header />
        <EmptyState title="Not connected">
          Set <span className="font-mono">MNEMOS_API_URL</span> to read sweep history.
        </EmptyState>
      </div>
    );
  }

  const [runsResult, findingsResult, proposalsResult] = await Promise.all([
    getCustodianRuns(15).catch(() => null),
    getCustodianFindings({ runId: selectedRun, limit: 60 }).catch(() => null),
    getProposals().catch(() => null),
  ]);

  const runs = runsResult?.runs ?? [];
  const findings = findingsResult?.findings ?? [];
  const proposals = proposalsResult?.proposals ?? [];

  const measuredCount = findings.filter((f) => f.measured).length;
  const skills = new Set(findings.map((f) => f.skill_id));

  return (
    <div className="flex flex-col gap-6">
      <Header />

      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardBody>
            <Stat label="Sweeps" value={formatCount(runs.length)} hint="Most recent first" />
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <Stat
              label="Findings"
              value={formatCount(findings.length)}
              accent="ember"
              hint={selectedRun ? "In the selected run" : "Across recent runs"}
            />
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <Stat
              label="Measured"
              value={`${measuredCount}/${findings.length || 0}`}
              accent="synapse"
              hint="Read from the control plane, no model involved"
            />
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <Stat
              label="Skills in play"
              value={formatCount(skills.size)}
              hint="CockroachDB's own published Agent Skills"
            />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Governance proposals</CardTitle>
          <span className="text-xs text-moonstone">the agent can ask; only a person can answer</span>
        </CardHeader>
        <CardBody>
          {proposals.length === 0 ? (
            <EmptyState title="No proposals outstanding">
              When the Custodian finds something it believes should be destroyed or quarantined, it
              records a proposal here. It has no code path that can execute one — approval routes
              through the Warden with dual control, and the audit row names both the proposing
              agent and the approving human.
            </EmptyState>
          ) : (
            <ul className="flex flex-col gap-3">
              {proposals.map((p) => (
                <li key={p.proposal_id} className="rounded border border-hairline bg-veil-hi p-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="rounded-sm border border-ember-edge bg-ember-fill px-2 py-0.5 text-xs font-medium text-ember">
                      {p.kind}
                    </span>
                    <span className="font-mono text-xs text-parchment">{p.target}</span>
                    <span className="ml-auto text-xs text-moonstone">{p.status}</span>
                  </div>
                  <p className="mt-2 text-sm text-moonstone">{p.rationale}</p>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
        <Card className="h-fit">
          <CardHeader>
            <CardTitle>Sweeps</CardTitle>
            {selectedRun ? (
              <Link href="/console/custodian" className="text-xs text-synapse hover:underline">
                Clear
              </Link>
            ) : null}
          </CardHeader>
          <CardBody className="flex flex-col gap-1.5">
            {runs.length === 0 ? (
              <p className="text-sm text-moonstone">No sweeps recorded yet.</p>
            ) : (
              runs.map((run) => <RunRow key={run.run_id} run={run} selected={selectedRun} />)
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{selectedRun ? "Findings in this sweep" : "Recent findings"}</CardTitle>
          </CardHeader>
          <CardBody>
            {findings.length === 0 ? (
              <EmptyState title="No findings">
                A sweep with nothing to report is a healthy outcome, not a failure to find
                something.
              </EmptyState>
            ) : (
              <ul className="flex flex-col gap-3">
                {findings.map((finding) => (
                  <FindingRow key={finding.finding_id} finding={finding} />
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function RunRow({ run, selected }: { run: CustodianRun; selected?: string }) {
  const Icon = TRIGGER_ICON[run.trigger_source] ?? CalendarClock;
  const isSelected = selected === run.run_id;
  const coverage = run.checks_run + run.checks_skipped;
  return (
    <Link
      href={`/console/custodian?run=${run.run_id}`}
      className={`flex flex-col gap-1 rounded border px-3 py-2 transition-colors ${
        isSelected
          ? "border-synapse-edge bg-synapse-fill"
          : "border-transparent hover:border-hairline hover:bg-veil-hi"
      }`}
    >
      <span className="flex items-center gap-2 text-sm text-parchment">
        <Icon className="size-3.5 text-moonstone" aria-hidden />
        {run.trigger_source}
        <span className="ml-auto text-xs text-dim">{relativeTime(run.started_at)}</span>
      </span>
      {run.trigger_detail ? (
        <span className="truncate font-mono text-xs text-ember">{run.trigger_detail}</span>
      ) : null}
      <span className="text-xs text-moonstone">
        {run.checks_run}/{coverage} checks ·{" "}
        <span className={run.status === "succeeded" ? "text-synapse" : "text-ember"}>
          {run.status}
        </span>
      </span>
    </Link>
  );
}

function FindingRow({ finding }: { finding: CustodianFinding }) {
  return (
    <li className="rounded-lg border border-hairline bg-veil-hi/40 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge severity={finding.severity} />
        {/* Measured vs interpreted, stated as evidence class rather than as a
            tool detail — see this file's module docstring. */}
        <span
          className={`inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 text-xs font-medium ${
            finding.measured
              ? "border-synapse-edge bg-synapse-fill text-synapse"
              : "border-umbra-edge bg-umbra-fill text-umbra"
          }`}
          title={
            finding.measured
              ? "A deterministic reading of control-plane data — no model in the loop. Enters memory as 'external'."
              : "The interpretation model's reading of tool output. Enters memory as 'agent', and can never corroborate itself."
          }
        >
          {finding.measured ? (
            <Ruler className="size-3" aria-hidden />
          ) : (
            <Bot className="size-3" aria-hidden />
          )}
          {finding.measured ? "measured" : "interpreted"}
        </span>
        <span className="ml-auto text-xs text-moonstone">{relativeTime(finding.created_at)}</span>
      </div>

      {/* The sentence a human reads is the point of this row; provenance
          metadata (which skill, which tool, the finding code) is real and
          worth keeping, but it is chrome around that sentence, not a peer of
          it — demoted below rather than competing above. */}
      <p className="mt-2 text-base text-parchment">{finding.summary}</p>
      {finding.recommendation ? (
        <p className="mt-1 text-sm text-moonstone">
          <span className="text-moonstone/70">Recommends: </span>
          {finding.recommendation}
        </p>
      ) : null}

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-dim">
        <span className="rounded-sm border border-hairline bg-veil px-1.5 py-px font-mono">
          {finding.skill_id}
        </span>
        <span>via {finding.tool_source}</span>
        <span className="font-mono">{finding.code}</span>
        {finding.fact_id ? (
          <>
            <span>·</span>
            <span>distilled into memory</span>
            <HashLink value={finding.fact_id} head={6} tail={4} />
          </>
        ) : null}
      </div>
    </li>
  );
}

function Header() {
  return (
    <header className="flex flex-col gap-1">
      <h1 className="font-display text-3xl tracking-[-0.02em]">Custodian</h1>
      <p className="max-w-prose text-sm text-moonstone">
        An agent that maintains the database using CockroachDB&rsquo;s own published Agent Skills —
        and that is held to the same trust rules as any other agent. It files findings into memory
        as <span className="font-mono">unverified</span>, and cannot promote them by repeating
        itself.
      </p>
    </header>
  );
}
