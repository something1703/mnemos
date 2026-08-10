import { BlastRadius } from "./blast-radius";

export const dynamic = "force-dynamic";

export default function BlastRadiusPage() {
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="font-display text-3xl tracking-[-0.02em]">Blast Radius</h1>
        <p className="max-w-prose text-sm text-moonstone">
          One poisoned source, and everything it touched — transitively, and{" "}
          <em>before</em> anything is acted on. Facts derived from it, skills citing those facts,
          recalls that returned them, actions taken on those recalls, and the episodes an agent
          wrote afterwards, where contamination hides behind impeccable-looking provenance.
        </p>
      </header>
      <BlastRadius />
    </div>
  );
}
