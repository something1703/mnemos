import Link from "next/link";
import { Globe2, ShieldCheck, GitBranch, ArrowRight } from "lucide-react";
import { getStats, getLedger } from "@/lib/api/queries";
import { apiConfigured } from "@/lib/api/server";
import { Button } from "@/components/ui/primitives";
import { CountUp } from "@/components/ui/count-up";
import { TrustLatticeDiagram } from "@/components/marketing/trust-lattice-diagram";
import { MemoryTrace, type TraceCommit } from "@/components/viz/memory-trace";
import type { LedgerOp } from "@/lib/brand";
import { cn } from "@/lib/utils";

export const revalidate = 30;

/** Tailwind cannot see a class name assembled with `${}` at build time — the
 * same escape hatch `ACCENT_CLASSES` (lib/brand.ts) documents, applied here
 * for the accents this page needs literally. One dot-color map reused by
 * both the pillar nodes and the plane rows, so "this accent, this meaning"
 * reads as the same visual language in both places rather than two. */
const ICON_ACCENT_CLASS = {
  synapse: "text-synapse",
  ember: "text-ember",
  signal: "text-signal",
} as const;

const ACCENT_BG = {
  synapse: "bg-synapse",
  ember: "bg-ember",
  parchment: "bg-moonstone",
  signal: "bg-signal",
  umbra: "bg-umbra",
} as const;

const PLANES = [
  {
    name: "The Fabric",
    role: "Episodic → semantic → procedural memory",
    llm: "no",
    accent: "ember" as const,
  },
  {
    name: "The Ledger",
    role: "Sharded hash chains, Merkle checkpoints anchored to S3 Object Lock",
    llm: "no",
    accent: "parchment" as const,
  },
  {
    name: "The Warden",
    role: "Residency, legal hold, erasure, blast-radius revocation",
    llm: "no — IAM, CI, and a runtime self-check enforce it",
    accent: "signal" as const,
  },
  {
    name: "The Custodian",
    role: "Runs official CockroachDB Agent Skills, files findings back into memory",
    llm: "yes — provider-agnostic, sandboxed to read-only tools",
    accent: "umbra" as const,
  },
];

/** Real, deployed infrastructure — not a stack list for its own sake. Each
 * reason is why that specific service is load-bearing, not decoration
 * (PRODUCT.md's Operating Context has the full account). Sponsor tech was
 * previously named twice on the whole site, both at the lowest-contrast
 * text weight — invisible on the one surface a judge actually evaluates. */
const BUILT_ON = [
  {
    name: "CockroachDB Cloud",
    reason: "Vectors, provenance graph, and audit ledger in one transactionally consistent database.",
  },
  {
    name: "AWS Lambda",
    reason: "The API and the sleep cycle — the write path does zero AI work.",
  },
  {
    name: "AWS ECS Fargate",
    reason: "The Custodian, scheduled and alarm-triggered — the one component with a model in it.",
  },
  {
    name: "S3 Object Lock",
    reason: "Compliance-mode WORM storage anchoring the ledger's Merkle checkpoints.",
  },
  {
    name: "AWS KMS",
    reason: "Per-tenant envelope encryption — the key a crypto-shred destroys.",
  },
];

const QUESTIONS = [
  {
    q: "Where does this memory legally live?",
    a: "An agent in Frankfurt recalls a patient record. Did that row cross a border? Most memory layers treat storage as one undifferentiated blob, so nobody knows.",
  },
  {
    q: "What did the agent believe, and why did it act?",
    a: "After an agent causes harm you need a deposition, not a log file. Today you get neither.",
  },
  {
    q: "One poisoned fact — what did it touch?",
    a: "Prompt injection that persists is agentic memory's defining security hole. No system can enumerate the damage, let alone undo it.",
  },
];

const PILLARS = [
  {
    icon: Globe2,
    title: "Residency",
    body: "REGIONAL BY ROW homes every episode to a jurisdiction. Raw content never crosses a border — only policy-approved projections do, and every crossing is logged with the policy that allowed it.",
    href: "/console/residency",
    accent: "synapse" as const,
  },
  {
    icon: GitBranch,
    title: "Accountability",
    body: "AS OF SYSTEM TIME reconstructs exactly what an agent believed at any past instant. explain(action_id) emits a deposition, hash-verified end to end, exportable as HTML that verifies itself offline.",
    href: "/console/deposition",
    accent: "ember" as const,
  },
  {
    icon: ShieldCheck,
    title: "Integrity",
    body: "Everything an LLM writes lands unverified and stays excluded from recall until independently corroborated. When a source turns out malicious, revoke_source() computes the blast radius and revokes it in one transaction.",
    href: "/console/blast-radius",
    accent: "signal" as const,
  },
];

export default async function LandingPage() {
  const configured = apiConfigured();
  const [stats, ledger] = configured
    ? await Promise.all([getStats().catch(() => null), getLedger(48).catch(() => null)])
    : [null, null];
  const commits: TraceCommit[] = (ledger?.entries ?? [])
    .slice()
    .reverse()
    .map((entry) => ({
      shard: entry.shard_id,
      seq: entry.seq,
      op: entry.op as LedgerOp,
      rowHash: entry.entry_hash,
      at: entry.committed_at,
    }));

  const liveStats = stats
    ? [
        {
          label: "facts trusted",
          value: (stats.facts_by_trust.trusted ?? 0) + (stats.facts_by_trust.corroborated ?? 0),
        },
        { label: "episodes recorded", value: stats.episodes },
        { label: "ledger entries", value: stats.chain_entries },
        { label: "chain shards", value: stats.posture.chain_shards },
      ]
    : [];

  return (
    <>
      {/* ------------------------------------------------------------ hero
          No second logo mark here — the nav directly above already carries
          it, and repeating the glyph, centered, at hero scale is exactly
          the template gesture: brand asserted twice in one viewport instead
          of once. The gradient wash makes DESIGN.md's own north star
          ("long-exposure neuron microscopy... deep indigo depths with
          bioluminescent traces") a rendered fact of the page rather than a
          claim in a markdown file the flat abyss background never paid
          off. The headline keeps the two-line brand copy verbatim
          (BRAND.md's positioning line) but stops setting both lines at
          identical weight — the second line steps down, which is a real
          typographic decision instead of the default "one giant centered
          block" every generated hero reaches for. The two calls to action
          are no longer a matched pair of pill buttons: one is the action,
          one is a way out of it. */}
      <section className="relative overflow-hidden">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[32rem]"
          style={{
            background:
              "radial-gradient(42% 55% at 20% 0%, rgb(111 227 210 / 16%), transparent 68%), radial-gradient(38% 46% at 86% 8%, rgb(139 127 212 / 13%), transparent 65%)",
          }}
        />
        <div className="mx-auto flex max-w-4xl flex-col items-center gap-8 px-5 pt-24 pb-10 text-center md:px-8 md:pt-32">
          <h1 className="flex flex-col items-center font-display tracking-[-0.02em]">
            <span className="text-4xl leading-[1.05] text-parchment md:text-6xl">
              Accountable memory
            </span>
            <span className="text-2xl leading-[1.15] text-moonstone md:text-4xl">for agents.</span>
          </h1>
          <p className="mx-auto max-w-2xl text-balance text-moonstone md:text-lg">
            Agents don&rsquo;t fail when they&rsquo;re wrong. They fail when they forget — or when
            they remember what they never should have.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-3">
            <Button asChild size="lg" variant="primary">
              <Link href="/console">
                Open the console <ArrowRight className="size-4" aria-hidden />
              </Link>
            </Button>
            <Link
              href="/how-it-works"
              className="text-sm text-moonstone underline decoration-hairline underline-offset-4 transition-colors hover:text-parchment"
            >
              See how it works
            </Link>
          </div>

          {/* The brand's own signature element (BRAND.md: "the one bold
              element... a live feed of the tenant's real ledger commits")
              had never once appeared outside /console — on the surface that
              actually has to make the case to a judge in the first few
              seconds, the most distinctive and least fakeable thing this
              product has was simply absent. This is the same MemoryTrace
              the console footer runs, fed by the same real data. Unboxed —
              a ledger tear-off between two hairlines, not another rounded
              card, and its own left-to-right draw-in is the hero's one
              motion moment, so nothing here wraps it in a second,
              redundant fade. */}
          {commits.length > 0 ? (
            <div className="w-full max-w-3xl border-y border-hairline py-3">
              <p className="mb-2 text-xs text-moonstone">
                The last {commits.length} rows committed to the audit chain — right now.
              </p>
              <MemoryTrace commits={commits} height={44} />
            </div>
          ) : null}
        </div>
      </section>

      {/* ------------------------------------------------------- live stats */}
      {liveStats.length > 0 ? (
        <section className="border-y border-hairline bg-veil/30">
          <div className="mx-auto flex max-w-4xl flex-wrap items-baseline justify-center gap-x-12 gap-y-5 px-5 py-10 md:px-8">
            {liveStats.map((s) => (
              <div key={s.label} className="flex items-baseline gap-2">
                <span className="tabular font-display text-2xl text-synapse md:text-3xl">
                  <CountUp value={s.value} />
                </span>
                <span className="text-xs text-moonstone">{s.label}</span>
              </div>
            ))}
          </div>
          <p className="pb-6 text-center text-xs text-moonstone">
            Live from the deployed instance — not a mockup.
          </p>
        </section>
      ) : null}

      {/* ------------------------------------------------------ questions */}
      <section className="mx-auto max-w-3xl px-5 py-20 md:px-8">
        <h2 className="mb-3 font-display text-2xl tracking-[-0.02em] text-parchment md:text-3xl">
          Three questions nobody can answer today
        </h2>
        <p className="mb-12 max-w-xl text-sm text-moonstone">
          Recall quality is crowded and roughly solved. These three are not — and Mnemos answers
          all three because the vectors, the provenance graph, and the audit ledger are one
          transactionally consistent database, not three services with a gap between them.
        </p>
        <div className="flex flex-col divide-y divide-hairline border-y border-hairline">
          {QUESTIONS.map((item) => (
            <div key={item.q} className="grid gap-2 py-8 md:grid-cols-[1fr_1.15fr] md:gap-10">
              <p className="font-display text-xl leading-snug text-parchment md:text-2xl">
                {item.q}
              </p>
              <p className="text-sm text-moonstone">{item.a}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------- pillars
          Nodes on a spine, not boxed icon-cards — the same dot-and-line
          grammar the memory trace and the four planes below both use, so
          "three pillars" reads as this product's own visual language
          rather than a stock feature-grid dressed in the brand's colors. */}
      <section className="border-t border-hairline bg-veil/20 py-24">
        <div className="mx-auto max-w-5xl px-5 md:px-8">
          <h2 className="mb-16 font-display text-2xl tracking-[-0.02em] text-parchment md:text-3xl">
            Three pillars
          </h2>
          <div className="relative grid gap-10 md:grid-cols-3 md:gap-8">
            <div aria-hidden className="absolute inset-x-0 top-0 hidden h-px bg-hairline md:block" />
            {PILLARS.map((p) => (
              <Link key={p.title} href={p.href} className="group relative flex flex-col gap-2.5">
                {/* A solid dot, painted after the spine in DOM order, covers
                    the 1px line beneath it — no color-matched ring needed to
                    fake a cutout against a translucent section background. */}
                <span
                  aria-hidden
                  className={cn(
                    "absolute -top-[29px] left-0 hidden size-3 rounded-full md:block",
                    ACCENT_BG[p.accent],
                  )}
                />
                <p.icon className={cn("size-5", ICON_ACCENT_CLASS[p.accent])} aria-hidden />
                <p className="font-display text-lg text-parchment">{p.title}</p>
                <p className="text-sm text-moonstone">{p.body}</p>
                <span className="flex items-center gap-1 pt-1 text-xs text-synapse opacity-0 transition-opacity group-hover:opacity-100">
                  Open in console <ArrowRight className="size-3" aria-hidden />
                </span>
              </Link>
            ))}
          </div>
        </div>
      </section>

      {/* ----------------------------------------------------- trust lattice */}
      <section className="mx-auto max-w-3xl px-5 py-20 md:px-8">
        <h2 className="mb-2 text-center font-display text-2xl tracking-[-0.02em] text-parchment md:text-3xl">
          Trust is earned, not declared
        </h2>
        <p className="mx-auto mb-12 max-w-xl text-center text-sm text-moonstone">
          Every claim an LLM writes lands unverified. The five states below are the whole trust
          lattice — the same states, colors, and shapes shown everywhere in the console.
        </p>
        <TrustLatticeDiagram />
      </section>

      {/* -------------------------------------------------------- four planes */}
      <section className="border-t border-hairline bg-veil/20 py-20">
        <div className="mx-auto max-w-4xl px-5 md:px-8">
          <h2 className="mb-2 text-center font-display text-2xl tracking-[-0.02em] text-parchment md:text-3xl">
            Four planes, one database
          </h2>
          <p className="mx-auto mb-12 max-w-xl text-center text-sm text-moonstone">
            The only component that can destroy memory has no model in it.
          </p>
          <div className="divide-y divide-hairline rounded-lg border border-hairline bg-veil">
            {PLANES.map((p) => (
              <div
                key={p.name}
                className="flex flex-col gap-1.5 px-5 py-4 md:flex-row md:items-baseline md:gap-6"
              >
                <span className="flex w-44 shrink-0 items-center gap-2">
                  <span
                    className={cn("size-2 shrink-0 rounded-full", ACCENT_BG[p.accent])}
                    aria-hidden
                  />
                  <span className="font-display text-sm text-parchment">{p.name}</span>
                </span>
                <span className="flex-1 text-xs text-moonstone md:text-sm">{p.role}</span>
                <span className="shrink-0 text-xs text-moonstone">
                  LLM: <span className="text-parchment">{p.llm}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* -------------------------------------------------------- built-on */}
      <section className="mx-auto max-w-4xl px-5 py-16 md:px-8">
        <h2 className="mb-10 text-center font-display text-2xl tracking-[-0.02em] text-parchment md:text-3xl">
          Built on
        </h2>
        <div className="flex flex-wrap justify-center gap-x-12 gap-y-8">
          {BUILT_ON.map((b) => (
            <div key={b.name} className="max-w-[13rem] text-center">
              <p className="font-display text-sm text-parchment">{b.name}</p>
              <p className="mt-1 text-xs text-moonstone">{b.reason}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ----------------------------------------------------------- closing */}
      <section className="mx-auto flex max-w-2xl flex-col items-center gap-6 px-5 py-24 text-center md:px-8">
        <h2 className="font-display text-2xl tracking-[-0.02em] text-parchment md:text-3xl">
          See it against real, deployed data.
        </h2>
        <p className="max-w-md text-sm text-moonstone">
          No mock data, no seeded demo you have to trust. The console reads the same live
          deployment this page just did.
        </p>
        <Button asChild size="lg" variant="primary">
          <Link href="/console">
            Open the console <ArrowRight className="size-4" aria-hidden />
          </Link>
        </Button>
      </section>
    </>
  );
}
