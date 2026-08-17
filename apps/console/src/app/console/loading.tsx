import { Loader2 } from "lucide-react";

/**
 * Every console route reads a remote Lambda; without this, navigation was a
 * silent stall — no feedback while the new screen's data loads, on a
 * product whose whole thesis is that visibility of system state matters.
 */
export default function ConsoleLoading() {
  return (
    <div className="flex items-center gap-2 py-12 text-sm text-moonstone">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      Loading…
    </div>
  );
}
