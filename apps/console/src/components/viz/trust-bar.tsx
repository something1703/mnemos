import { TRUST_ORDER, TRUST_PRESENTATION, type TrustState } from "@/lib/brand";
import { TrustBadge } from "@/components/ui/trust-badge";
import { formatCount } from "@/lib/utils";

const ACCENT_VAR: Record<string, string> = {
  synapse: "var(--synapse)",
  ember: "var(--ember)",
  signal: "var(--signal)",
  umbra: "var(--umbra)",
  parchment: "var(--parchment)",
};

/**
 * The trust distribution, as a stacked bar "that makes `unverified` volume
 * impossible to ignore" (Phase 08.2).
 *
 * Two decisions do that work. The bar is ordered believed → doubted, so the
 * doubted mass always accumulates on the same side and its width is the
 * headline. And the legend states counts, not percentages: "5 unverified" is
 * a number someone can act on; "23%" invites rounding it away.
 */
export function TrustBar({ counts }: { counts: Partial<Record<TrustState, number>> }) {
  const total = TRUST_ORDER.reduce((sum, state) => sum + (counts[state] ?? 0), 0);

  if (total === 0) {
    return <p className="text-sm text-moonstone">No facts yet — nothing to distribute.</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      <div
        className="flex h-3 w-full overflow-hidden rounded-full border border-hairline"
        role="img"
        aria-label={TRUST_ORDER.filter((s) => counts[s])
          .map((s) => `${counts[s]} ${TRUST_PRESENTATION[s].label}`)
          .join(", ")}
      >
        {TRUST_ORDER.map((state) => {
          const count = counts[state] ?? 0;
          if (count === 0) return null;
          return (
            <span
              key={state}
              style={{
                width: `${(count / total) * 100}%`,
                background: ACCENT_VAR[TRUST_PRESENTATION[state].accent],
              }}
              title={`${count} ${TRUST_PRESENTATION[state].label}`}
            />
          );
        })}
      </div>

      <ul className="flex flex-wrap items-center gap-x-4 gap-y-2">
        {TRUST_ORDER.map((state) => {
          const count = counts[state] ?? 0;
          if (count === 0) return null;
          return (
            <li key={state} className="flex items-center gap-2">
              <TrustBadge trust={state} />
              <span className="tabular text-sm text-parchment">{formatCount(count)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
