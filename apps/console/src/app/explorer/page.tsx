import Link from "next/link";
import { recall } from "@/lib/api/mcp";
import { apiConfigured } from "@/lib/api/server";
import { Card, CardBody, EmptyState } from "@/components/ui/primitives";
import { FactCard } from "@/components/fact-card";
import { SearchForm } from "./search-form";

export const dynamic = "force-dynamic";

/**
 * Memory Explorer — the search box IS live hybrid recall (Phase 08.2), the
 * same `recall` MCP tool an agent calls, not a separate text search over the
 * same rows. Anything else would let the console show ranking behaviour the
 * agent does not actually get.
 *
 * State lives in the URL (`?q=&unverified=`) rather than in a client store:
 * a judge can send someone a link to the exact query they are looking at, and
 * the whole screen stays a server component.
 */
export default async function ExplorerPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; unverified?: string }>;
}) {
  const params = await searchParams;
  const query = params.q?.trim() ?? "";
  const includeUnverified = params.unverified === "1";

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-display text-3xl tracking-[-0.02em]">Memory Explorer</h1>
        <p className="max-w-prose text-sm text-moonstone">
          Ask what an agent would ask. This runs the real <span className="font-mono">recall</span>{" "}
          tool, so what you see here is exactly what an agent would have been handed.
        </p>
      </header>

      <SearchForm query={query} includeUnverified={includeUnverified} />

      {!apiConfigured() ? (
        <EmptyState title="Not connected">
          Set <span className="font-mono">MNEMOS_API_URL</span> and{" "}
          <span className="font-mono">MNEMOS_API_KEY_READ</span> in{" "}
          <span className="font-mono">apps/console/.env.local</span>.
        </EmptyState>
      ) : query ? (
        <Results query={query} includeUnverified={includeUnverified} />
      ) : (
        <EmptyState title="Ask a question to see what memory answers">
          Recall is hybrid — vector similarity and lexical match, re-ranked by how much the system
          actually believes each fact. Try{" "}
          <Link href="/explorer?q=is+the+database+cluster+healthy%3F" className="text-synapse hover:underline">
            “is the database cluster healthy?”
          </Link>{" "}
          to see the Custodian&rsquo;s own corroborated finding come back.
        </EmptyState>
      )}
    </div>
  );
}

async function Results({
  query,
  includeUnverified,
}: {
  query: string;
  includeUnverified: boolean;
}) {
  let result;
  try {
    result = await recall(query, { k: 12, includeUnverified });
  } catch (cause) {
    return (
      <EmptyState title="Recall failed">
        {cause instanceof Error ? cause.message : "The API did not answer."}
      </EmptyState>
    );
  }

  const facts = result.facts ?? [];
  const withheld = result.withheld ?? null;
  const withheldTotal = withheld
    ? Object.values(withheld).reduce<number>((sum, n) => sum + (n ?? 0), 0)
    : 0;

  return (
    <div className="flex flex-col gap-4">
      {/* The disclosure that stops a filtered result being misread as an empty
          one. It is deliberately not hidden behind a toggle. */}
      {withheldTotal > 0 ? (
        <Card className="border-umbra-edge bg-umbra-fill">
          <CardBody className="flex flex-wrap items-center gap-x-3 gap-y-1 py-3 text-sm">
            <span className="font-medium text-umbra">
              {withheldTotal} result{withheldTotal === 1 ? "" : "s"} withheld
            </span>
            <span className="text-moonstone">
              {Object.entries(withheld ?? {})
                .filter(([, n]) => n)
                .map(([reason, n]) => `${n} ${reason}`)
                .join(" · ")}
            </span>
            {!includeUnverified && withheld?.unverified ? (
              <Link
                href={`/explorer?q=${encodeURIComponent(query)}&unverified=1`}
                className="ml-auto text-xs text-synapse hover:underline"
              >
                Show unverified too →
              </Link>
            ) : null}
          </CardBody>
        </Card>
      ) : null}

      {facts.length === 0 ? (
        <EmptyState title="Nothing recalled for that query">
          Memory answered honestly rather than guessing. Try a broader question, or include
          unverified facts to see what has been written but not yet corroborated.
        </EmptyState>
      ) : (
        <>
          <p className="text-xs text-dim">
            {facts.length} fact{facts.length === 1 ? "" : "s"}, ranked by similarity × strength ×
            confidence × trust
          </p>
          <div className="flex flex-col gap-3">
            {facts.map((fact) => (
              <FactCard key={fact.fact_id} fact={fact} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
