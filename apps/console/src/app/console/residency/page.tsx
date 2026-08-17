import { getCrossings, getFacts, getHolds } from "@/lib/api/queries";
import { apiConfigured } from "@/lib/api/server";
import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  RegionChip,
  Stat,
} from "@/components/ui/primitives";
import { formatCount, relativeTime } from "@/lib/utils";

export const revalidate = 10;

/**
 * Residency — where memory physically lives, and what was stopped at a border.
 *
 * A region-lane diagram rather than a decorative globe (Phase 08.2): a globe
 * implies a geography this system does not model. What it models is a set of
 * named regions, a home region per row, and a projection policy per boundary.
 *
 * Denied crossings render in umbra alongside permitted ones, because a
 * residency log that recorded only its successes cannot answer the question an
 * auditor actually asks — which is what you stopped.
 */
export default async function ResidencyPage() {
  if (!apiConfigured()) {
    return (
      <div className="flex flex-col gap-6">
        <Header />
        <EmptyState title="Not connected">
          Set <span className="font-mono">MNEMOS_API_URL</span> to read residency data.
        </EmptyState>
      </div>
    );
  }

  const [crossingsResult, factsResult, holdsResult] = await Promise.all([
    getCrossings(50).catch(() => null),
    getFacts({ limit: 200 }).catch(() => null),
    getHolds().catch(() => null),
  ]);

  const crossings = crossingsResult?.crossings ?? [];
  const facts = factsResult?.facts ?? [];
  const holds = holdsResult?.holds ?? [];

  const byRegion = new Map<string, number>();
  for (const fact of facts) {
    byRegion.set(fact.home_region, (byRegion.get(fact.home_region) ?? 0) + 1);
  }
  const regions = [...byRegion.entries()].sort((a, b) => b[1] - a[1]);
  const denied = crossings.filter((c) => !c.allowed);

  return (
    <div className="flex flex-col gap-6">
      <Header />

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardBody>
            <Stat
              label="Regions in use"
              value={formatCount(regions.length)}
              hint="Home regions actually holding rows"
            />
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <Stat
              label="Crossings logged"
              value={formatCount(crossings.length)}
              accent="ember"
              hint="Permitted and refused, both recorded"
            />
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <Stat
              label="Refused"
              value={formatCount(denied.length)}
              accent={denied.length > 0 ? "umbra" : "parchment"}
              hint="A residency control with no refusals is untested, not perfect"
            />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Region lanes</CardTitle>
          <span className="text-xs text-moonstone">where facts are homed right now</span>
        </CardHeader>
        <CardBody>
          {regions.length === 0 ? (
            <EmptyState title="No facts to place">
              Nothing is homed yet, so there is no residency to show.
            </EmptyState>
          ) : (
            <ul className="flex flex-col gap-3">
              {regions.map(([region, count]) => {
                const max = regions[0][1] || 1;
                return (
                  <li key={region} className="flex items-center gap-3">
                    <span className="w-44 shrink-0">
                      <RegionChip region={region} />
                    </span>
                    <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-veil-hi">
                      <div
                        className="h-full rounded-full bg-synapse/70"
                        style={{ width: `${(count / max) * 100}%` }}
                      />
                    </div>
                    <span className="tabular w-14 shrink-0 text-right font-mono text-sm text-parchment">
                      {count}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          <p className="mt-4 text-xs text-moonstone">
            The deployed cluster is single-region, so every row is homed in{" "}
            <span className="font-mono">us-east-1</span> and the enforcement you can see here is
            the Warden&rsquo;s read-path projection. The multi-region homing story
            (<span className="font-mono">REGIONAL BY ROW</span> across 9 nodes and 3 localities)
            runs on the local rig — <span className="font-mono">make db-multiregion</span> — and is
            a demonstrated design rather than a claim about this deployment.
          </p>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Border crossings</CardTitle>
          <span className="text-xs text-moonstone">refusals shown in umbra</span>
        </CardHeader>
        <CardBody>
          {crossings.length === 0 ? (
            <EmptyState title="No crossings recorded">
              Nothing has attempted to read a subject&rsquo;s memory from outside its home region
              yet. Attempted denials would appear here with the policy that refused them.
            </EmptyState>
          ) : (
            <ul className="flex flex-col">
              {crossings.map((c, i) => (
                <li
                  key={`${c.subject_key}-${c.occurred_at}-${i}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-hairline/50 py-2 text-sm last:border-0"
                >
                  <span
                    className={`w-20 shrink-0 text-xs font-medium ${
                      c.allowed ? "text-synapse" : "text-umbra"
                    }`}
                  >
                    {c.allowed ? "permitted" : "refused"}
                  </span>
                  <span className="font-mono text-xs text-moonstone">
                    {c.from_region} → {c.to_region}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-parchment">
                    {c.subject_key}
                  </span>
                  <span className="text-xs text-moonstone">{c.policy_applied ?? c.projection ?? "—"}</span>
                  <span className="w-24 shrink-0 text-right text-xs text-dim">
                    {relativeTime(c.occurred_at)}
                  </span>
                  {c.denied_reason ? (
                    <p className="w-full text-xs text-umbra">{c.denied_reason}</p>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Legal holds</CardTitle>
          <span className="text-xs text-moonstone">erasure is refused while one stands</span>
        </CardHeader>
        <CardBody>
          {holds.length === 0 ? (
            <EmptyState title="No holds on record">
              A hold makes erasure refuse loudly and cite the matter reference, rather than
              silently succeeding.
            </EmptyState>
          ) : (
            <ul className="flex flex-col">
              {holds.map((hold) => (
                <li
                  key={hold.hold_id}
                  className="flex flex-wrap items-center gap-x-3 border-b border-hairline/50 py-2 text-sm last:border-0"
                >
                  <span
                    className={`w-16 shrink-0 text-xs font-medium ${
                      hold.active ? "text-ember" : "text-dim"
                    }`}
                  >
                    {hold.active ? "active" : "released"}
                  </span>
                  <span className="font-mono text-xs text-parchment">{hold.subject_key}</span>
                  <span className="text-xs text-moonstone">{hold.matter_reference}</span>
                  <span className="ml-auto text-xs text-dim">{relativeTime(hold.placed_at)}</span>
                </li>
              ))}
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
      <h1 className="font-display text-3xl tracking-[-0.02em]">Residency</h1>
      <p className="max-w-prose text-sm text-moonstone">
        Where each subject&rsquo;s memory physically lives, the policy governing every boundary,
        and — the part that actually matters to an auditor — what was refused.
      </p>
    </header>
  );
}
