import { Shell } from "@/components/shell";
import { LiveRefresh } from "@/components/live-refresh";

/**
 * Everything under /console is the operating dashboard — the nine screens
 * Phase 08.2 specifies, behind the nav rail and the live memory-trace
 * footer. Kept apart from the root layout so the marketing site
 * (`(marketing)/`) can render its own header instead of the dashboard chrome.
 */
export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <LiveRefresh />
      <Shell>{children}</Shell>
    </>
  );
}
