import { TrustBadge } from "@/components/ui/trust-badge";
import { RegionChip } from "@/components/ui/primitives";
import { HashLink } from "@/components/ui/hash-link";
import type { RecalledFact } from "@/lib/api/mcp";

/**
 * One recalled fact, with the arithmetic that ranked it.
 *
 * The score breakdown is not a debug affordance — it is the product. A recall
 * result that just asserts an order asks to be trusted; one that shows
 * similarity x strength x confidence x trust_weight can be argued with. The
 * factors render as a row of bars so a low trust_weight is visible at a glance
 * rather than needing to be read.
 */
export function FactCard({ fact }: { fact: RecalledFact }) {
  const b = fact.score_breakdown;
  return (
    <article className="rounded-lg border border-hairline bg-veil transition-colors hover:border-synapse-edge">
      <div className="flex flex-col gap-3 px-4 py-3.5">
        <div className="flex flex-wrap items-center gap-2">
          <TrustBadge trust={fact.trust} />
          <span className="rounded-sm border border-hairline bg-veil-hi px-2 py-0.5 font-mono text-xs text-moonstone">
            {fact.fact_kind}
          </span>
          <RegionChip region={fact.home_region} />
          <span className="ml-auto text-xs text-dim">
            score <span className="tabular font-mono text-parchment">{fact.score.toFixed(3)}</span>
          </span>
        </div>

        <p className="text-sm leading-relaxed text-parchment">
          {fact.text ?? (
            /* Deliberately does NOT claim "shredded". A null text means the
             * ciphertext would not decrypt with the key this deployment holds,
             * and crypto-shredding is only one reason for that — a rotated or
             * lost master key looks identical from here. Asserting the cause
             * would be the console inventing evidence, which is the one thing
             * an accountability product must never do. */
            <span className="text-dim italic">
              Content could not be decrypted with this deployment&rsquo;s key. The fact and its
              provenance remain on the record; only the plaintext is unreadable.
            </span>
          )}
        </p>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-dim">
          <span className="font-mono">{fact.subject_key}</span>
          <span>·</span>
          <span>
            {fact.corroboration_count} independent{" "}
            {fact.corroboration_count === 1 ? "source" : "sources"}
          </span>
          <span>·</span>
          <span>{fact.provenance.length} provenance edge{fact.provenance.length === 1 ? "" : "s"}</span>
          <HashLink value={fact.fact_id} className="ml-auto" />
        </div>

        <dl className="grid grid-cols-4 gap-3 border-t border-hairline pt-3">
          <Factor label="similarity" value={b.similarity} />
          <Factor label="strength" value={b.strength} max={2} />
          <Factor label="confidence" value={b.confidence} />
          <Factor label="trust" value={b.trust_weight} />
        </dl>
      </div>
    </article>
  );
}

function Factor({ label, value, max = 1 }: { label: string; value: number; max?: number }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-[10px] tracking-wide text-dim uppercase">{label}</dt>
      <dd className="tabular font-mono text-xs text-moonstone">{value.toFixed(2)}</dd>
      <div className="h-1 overflow-hidden rounded-full bg-veil-hi" aria-hidden>
        <div className="h-full rounded-full bg-synapse/70" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
