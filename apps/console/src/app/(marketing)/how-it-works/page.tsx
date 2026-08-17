import Link from "next/link";
import type { Metadata } from "next";
import { ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/primitives";
import { LifecycleDiagram } from "@/components/marketing/lifecycle-diagram";

export const metadata: Metadata = {
  title: "How it works",
  description:
    "The five invariants, the memory lifecycle, and the corroboration gate that makes Mnemos poison-resistant.",
};

const INVARIANTS = [
  {
    n: "01",
    title: "No LLM-driven process holds DELETE or governance privileges",
    by: "Database role grants; the Warden contains zero LLM SDK dependency, checked statically in CI",
  },
  {
    n: "02",
    title: "Every state-changing memory op appends a hash-chained audit row in the same transaction",
    by: "A BEFORE INSERT/UPDATE/DELETE trigger refuses the mutation unless an audit ticket exists in the same transaction",
  },
  {
    n: "03",
    title: "No fact becomes recallable without provenance to at least one episode",
    by: "A trigger on semantic_facts blocks promotion with zero provenance edges",
  },
  {
    n: "04",
    title: "Memory rows never leave their home region",
    by: "REGIONAL BY ROW homing; enforced read-path projection on recall()/recall_as_of()",
  },
  {
    n: "05",
    title: "Erasure is atomic across rows, vectors, and provenance — or it does not happen",
    by: "One serializable transaction per erasure mode; legal hold checked before every erasure",
  },
];

export default function HowItWorksPage() {
  return (
    <>
      <section className="mx-auto max-w-3xl px-5 pt-20 pb-12 text-center md:px-8">
        <h1 className="mb-4 font-display text-4xl tracking-[-0.02em] text-parchment md:text-5xl">
          How it works
        </h1>
        <p className="mx-auto max-w-xl text-moonstone">
          Not a vector database with a memory label on it. A trust lattice, a jurisdiction, and a
          tamper-evident record — built into one transactionally consistent database rather than
          bolted on as three separate services.
        </p>
      </section>

      {/* --------------------------------------------------------- lifecycle */}
      <section className="border-y border-hairline bg-veil/20 py-16">
        <div className="mx-auto max-w-4xl px-5 md:px-8">
          <h2 className="mb-2 text-center font-display text-2xl tracking-[-0.02em] text-parchment">
            From one episode to a recallable fact
          </h2>
          <p className="mx-auto mb-10 max-w-xl text-center text-sm text-moonstone">
            The write path does zero AI work — memory intake survives a total model-provider
            outage. All interpretation happens asynchronously, after the fact.
          </p>
          <LifecycleDiagram />
        </div>
      </section>

      {/* -------------------------------------------------------- corroboration */}
      <section className="mx-auto max-w-3xl px-5 py-16 md:px-8">
        <h2 className="mb-4 font-display text-2xl tracking-[-0.02em] text-parchment">
          Corroboration is arithmetic, not judgement
        </h2>
        <div className="flex flex-col gap-4 text-sm text-moonstone">
          <p>
            A fact promotes from <code className="rounded-sm bg-veil-hi px-1 py-0.5 font-mono text-xs text-parchment">unverified</code>{" "}
            to <code className="rounded-sm bg-veil-hi px-1 py-0.5 font-mono text-xs text-parchment">corroborated</code>{" "}
            only when its provenance touches at least two{" "}
            <strong className="text-parchment">independent</strong> sources — a different session{" "}
            <em>and</em> a different origin category (system, operator, agent, or external).
            Counting is exact maximum bipartite matching over a fact&rsquo;s provenance edges, not
            a heuristic: sessions on one side, the four origin categories on the other, an edge
            wherever a session contributed that category at least once.
          </p>
          <p>
            This is why an agent can never promote its own claim by repeating it. Ten episodes
            from the same channel, in ten different sessions, still fill only one slot in the
            matching — the collusion threshold is published, not implied to be infinite: an
            attacker who controls two genuinely independent-looking sources can promote a fact.
            Raising that threshold is a tenant-configurable policy, and it trades poisoning
            resistance against how long legitimate knowledge takes to become usable.
          </p>
          <p>
            <code className="rounded-sm bg-veil-hi px-1 py-0.5 font-mono text-xs text-parchment">system</code>{" "}
            and <code className="rounded-sm bg-veil-hi px-1 py-0.5 font-mono text-xs text-parchment">operator</code>{" "}
            provenance is trusted on arrival — one such episode promotes a fact directly, no
            corroboration needed. That is also why declaring either one is bound to an admin
            credential rather than left to whatever an agent claims about its own output: an
            agent that could simply say &ldquo;record this as operator&rdquo; would defeat the
            gate in a single call.
          </p>
        </div>
      </section>

      {/* -------------------------------------------------------- invariants */}
      <section className="border-t border-hairline bg-veil/20 py-16">
        <div className="mx-auto max-w-3xl px-5 md:px-8">
          <h2 className="mb-2 text-center font-display text-2xl tracking-[-0.02em] text-parchment">
            Five invariants, each with a named test
          </h2>
          <p className="mx-auto mb-10 max-w-xl text-center text-sm text-moonstone">
            Not asserted in prose. Every one below has a test file that fails the build if it
            stops being true.
          </p>
          <div className="divide-y divide-hairline border-y border-hairline">
            {INVARIANTS.map((inv) => (
              <div key={inv.n} className="flex items-start gap-4 py-4">
                <span className="mt-0.5 shrink-0 font-mono text-xs text-synapse">{inv.n}</span>
                <div className="flex flex-col gap-1">
                  <p className="text-sm text-parchment">{inv.title}</p>
                  <p className="text-xs text-moonstone">{inv.by}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* -------------------------------------------------------- containment */}
      <section className="mx-auto max-w-3xl px-5 py-16 md:px-8">
        <h2 className="mb-4 font-display text-2xl tracking-[-0.02em] text-parchment">
          Prevention is bounded. Containment is the real control.
        </h2>
        <p className="text-sm text-moonstone">
          Prompt-level defenses fail eventually — content is delimited and declared as data in
          every prompt, but that is not treated as sufficient on its own. When a source turns out
          to be malicious, <code className="rounded-sm bg-veil-hi px-1 py-0.5 font-mono text-xs text-parchment">revoke_source()</code>{" "}
          computes the transitive blast radius — facts, corroborations, skills, past recalls, and
          declared actions it touched — and revokes all of it in one serializable transaction. A
          fact corroborated <em>only</em> by the revoked source&rsquo;s descendants is quarantined; a
          fact also corroborated by genuinely independent evidence survives, demoted to whatever
          that remaining evidence actually earns. Depositions for affected decisions read: this
          decision was influenced by subsequently-revoked memory.
        </p>
      </section>

      <section className="mx-auto flex max-w-xl flex-col items-center gap-6 px-5 py-20 text-center md:px-8">
        <Button asChild size="lg" variant="primary">
          <Link href="/console/blast-radius">
            See blast-radius revocation live <ArrowRight className="size-4" aria-hidden />
          </Link>
        </Button>
      </section>
    </>
  );
}
