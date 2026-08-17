"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { Search } from "lucide-react";
import { Button, Card, CardBody, CardHeader, CardTitle, EmptyState } from "@/components/ui/primitives";
import { TrustBadge } from "@/components/ui/trust-badge";
import type { RecallResult, RecalledFact } from "@/lib/api/mcp";
import type { TrustState } from "@/lib/brand";

/**
 * The scrubber and the diff.
 *
 * Two design decisions do the heavy lifting:
 *
 * 1. The addressable window is drawn to scale and the rest of the timeline is
 *    greyed with `gc.ttlseconds` stated in words. A scrubber that let you drag
 *    into unaddressable time and then errored would teach the wrong thing
 *    about why — the limit is CockroachDB's garbage collection, not a product
 *    choice, and the UI should make that legible before the click.
 *
 * 2. The result is rendered as a DIFF against now, not as a second list.
 *    "Watching an agent's mind change" only reads if the change is what is
 *    highlighted: gone, trust-changed, or unchanged.
 */
export function TimeMachine({
  query,
  asOf,
  now,
  past,
  pastError,
  gcSeconds,
  chainEntries,
}: {
  query: string;
  asOf: string | null;
  now: RecallResult | null;
  past: RecallResult | null;
  pastError: string | null;
  gcSeconds: number;
  chainEntries: number;
}) {
  const router = useRouter();
  const [value, setValue] = useState(query);
  const [offset, setOffset] = useState(() => {
    if (!asOf) return 0;
    return Math.round((Date.now() - new Date(asOf).getTime()) / 1000);
  });
  const [pending, startTransition] = useTransition();

  function go(nextOffset: number) {
    const params = new URLSearchParams();
    if (value.trim()) params.set("q", value.trim());
    if (nextOffset > 0) {
      params.set("t", new Date(Date.now() - nextOffset * 1000).toISOString());
    }
    startTransition(() => router.push(`/console/time-machine?${params.toString()}`));
  }

  const diff = useMemo(() => computeDiff(now?.facts ?? [], past?.facts ?? []), [now, past]);

  return (
    <div className="flex flex-col gap-5">
      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          go(offset);
        }}
        role="search"
      >
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-dim"
            aria-hidden
          />
          <input
            type="search"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="Ask a question, then scrub backwards"
            aria-label="Query to ask of the past"
            className="h-11 w-full rounded border border-hairline bg-veil pr-3 pl-9 text-sm text-parchment placeholder:text-dim"
          />
        </div>
        <Button type="submit" variant="primary" size="lg" disabled={pending}>
          {pending ? "Recalling…" : "Ask"}
        </Button>
      </form>

      <Card>
        <CardHeader>
          <CardTitle>Temporal window</CardTitle>
          <span className="font-mono text-xs text-moonstone">
            gc.ttlseconds = {gcSeconds.toLocaleString()}
          </span>
        </CardHeader>
        <CardBody className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            {/* The slider runs left = oldest addressable, right = now, because
                that is how every timeline the user has ever seen runs. The
                stored state is an OFFSET (seconds into the past), so the two
                are inverses of each other — getting this backwards put the
                handle on "now" under a label reading "75m ago". */}
            <input
              type="range"
              min={0}
              max={gcSeconds}
              step={30}
              value={gcSeconds - Math.min(offset, gcSeconds)}
              onChange={(event) => setOffset(gcSeconds - Number(event.target.value))}
              onMouseUp={() => go(offset)}
              onTouchEnd={() => go(offset)}
              onKeyUp={(event) => {
                if (event.key.startsWith("Arrow")) go(offset);
              }}
              aria-label={`How far back to ask: ${formatOffset(offset)} ago`}
              aria-valuetext={offset === 0 ? "now" : `${formatOffset(offset)} ago`}
              className="w-full accent-[var(--synapse)]"
            />
            <div className="flex justify-between text-xs text-moonstone">
              <span>{formatOffset(gcSeconds)} ago — the GC boundary</span>
              <span>now</span>
            </div>
            <p className="text-sm">
              <span className="text-moonstone">Asking as of </span>
              <span className="font-medium text-parchment">
                {offset === 0 ? "now" : `${formatOffset(offset)} ago`}
              </span>
            </p>
          </div>

          <p className="text-xs text-moonstone">
            Everything older than{" "}
            <span className="text-parchment">{formatOffset(gcSeconds)}</span> is unaddressable:
            CockroachDB garbage-collects MVCC history past{" "}
            <span className="font-mono">gc.ttlseconds</span>, so the database genuinely cannot
            answer for it. That boundary is the cluster&rsquo;s, not this console&rsquo;s — the
            audit chain still covers the whole period ({chainEntries.toLocaleString()} entries),
            because the ledger is append-only rather than time-travelled.
          </p>

          {/* The timestamp actually sent, not one recomputed at render — those
              drift apart by however long the round trip took, and on a screen
              about temporal precision that difference is the whole point. */}
          {asOf ? (
            <p className="font-mono text-xs break-all text-synapse">
              AS OF SYSTEM TIME &lsquo;{asOf}&rsquo;
            </p>
          ) : null}
        </CardBody>
      </Card>

      {!query ? (
        <EmptyState title="Ask something first">
          Type a question above, then drag the scrubber. The answer re-renders for the moment you
          land on.
        </EmptyState>
      ) : pastError ? (
        <Card className="border-ember-edge bg-ember-fill">
          <CardBody>
            <p className="text-sm font-medium text-ember">The database refused that moment</p>
            {/* Verbatim, because the typed error carries the REAL boundary and
                paraphrasing it would lose the number that matters. */}
            <p className="mt-1 font-mono text-xs text-moonstone">{pastError}</p>
          </CardBody>
        </Card>
      ) : !asOf ? (
        <EmptyState title="Scrub backwards to compare">
          Right now you are looking at the present. Drag the slider to ask the same question of an
          earlier moment.
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-3">
          <p className="text-xs text-moonstone">
            Comparing <span className="text-parchment">{formatOffset(offset)} ago</span> against now
          </p>
          {diff.length === 0 ? (
            <EmptyState title="Nothing to compare">
              Neither moment returned a fact for that question.
            </EmptyState>
          ) : (
            diff.map((row) => <DiffRow key={row.fact.fact_id} row={row} />)
          )}
        </div>
      )}
    </div>
  );
}

type DiffKind = "unchanged" | "trust-changed" | "gone-since" | "new-since";

interface DiffRow {
  fact: RecalledFact;
  kind: DiffKind;
  thenTrust?: TrustState;
  nowTrust?: TrustState;
}

function computeDiff(nowFacts: RecalledFact[], pastFacts: RecalledFact[]): DiffRow[] {
  const byIdNow = new Map(nowFacts.map((f) => [f.fact_id, f]));
  const byIdPast = new Map(pastFacts.map((f) => [f.fact_id, f]));
  const rows: DiffRow[] = [];

  for (const [id, thenFact] of byIdPast) {
    const nowFact = byIdNow.get(id);
    if (!nowFact) {
      rows.push({ fact: thenFact, kind: "gone-since", thenTrust: thenFact.trust });
    } else if (nowFact.trust !== thenFact.trust) {
      rows.push({
        fact: nowFact,
        kind: "trust-changed",
        thenTrust: thenFact.trust,
        nowTrust: nowFact.trust,
      });
    } else {
      rows.push({ fact: nowFact, kind: "unchanged" });
    }
  }
  for (const [id, nowFact] of byIdNow) {
    if (!byIdPast.has(id)) rows.push({ fact: nowFact, kind: "new-since" });
  }

  const rank: Record<DiffKind, number> = {
    "trust-changed": 0,
    "new-since": 1,
    "gone-since": 2,
    unchanged: 3,
  };
  return rows.sort((a, b) => rank[a.kind] - rank[b.kind]);
}

const KIND_COPY: Record<DiffKind, { label: string; className: string }> = {
  "trust-changed": {
    label: "Trust changed",
    className: "border-ember-edge bg-ember-fill text-ember",
  },
  "new-since": {
    label: "Learned since",
    className: "border-synapse-edge bg-synapse-fill text-synapse",
  },
  "gone-since": {
    label: "No longer returned",
    className: "border-signal-edge bg-signal-fill text-signal",
  },
  unchanged: { label: "Unchanged", className: "border-hairline bg-veil-hi text-moonstone" },
};

function DiffRow({ row }: { row: DiffRow }) {
  const copy = KIND_COPY[row.kind];
  return (
    <article className="rounded-lg border border-hairline bg-veil px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-sm border px-2 py-0.5 text-xs font-medium ${copy.className}`}>
          {copy.label}
        </span>
        {row.kind === "trust-changed" && row.thenTrust && row.nowTrust ? (
          <span className="flex items-center gap-2">
            <TrustBadge trust={row.thenTrust} />
            <span className="text-dim">→</span>
            <TrustBadge trust={row.nowTrust} />
          </span>
        ) : (
          <TrustBadge trust={row.fact.trust} />
        )}
        <span className="ml-auto font-mono text-xs text-moonstone">{row.fact.subject_key}</span>
      </div>
      <p className="mt-2 text-sm text-parchment">
        {row.fact.text ?? <span className="text-moonstone italic">content not decryptable</span>}
      </p>
    </article>
  );
}

function formatOffset(seconds: number): string {
  if (seconds <= 0) return "now";
  if (seconds < 90) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes}m`;
  return `${(minutes / 60).toFixed(1)}h`;
}
