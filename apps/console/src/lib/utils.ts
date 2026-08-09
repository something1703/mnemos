import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/** The standard shadcn/cva class combiner: clsx for conditionals, tailwind-merge
 * so a caller's `className` can override a component's own utilities instead of
 * losing to specificity ties. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Middle-truncate a hash or id: keeps both ends, which is what makes two
 * hashes distinguishable at a glance. Never truncates to fewer than head+tail. */
export function middleTruncate(value: string, head = 8, tail = 6): string {
  if (value.length <= head + tail + 1) return value;
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}

export function formatCount(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}

/** Relative time that stays honest about precision — "3m ago", not "3 minutes
 * and 14 seconds ago", and an absolute timestamp is always available in the
 * title attribute rather than being replaced by the relative one. */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 45) return "just now";
  const units: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"],
    [3600, "minute"],
    [86400, "hour"],
    [2592000, "day"],
  ];
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (seconds < 60) return rtf.format(-seconds, "second");
  if (seconds < 3600) return rtf.format(-Math.round(seconds / 60), "minute");
  if (seconds < 86400) return rtf.format(-Math.round(seconds / 3600), "hour");
  void units;
  return rtf.format(-Math.round(seconds / 86400), "day");
}
