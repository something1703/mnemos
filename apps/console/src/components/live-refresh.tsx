"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

/**
 * Makes the console actually live, not just labelled that way.
 *
 * Found during the design review: every console page sets `revalidate`
 * (ISR cache lifetime — how stale a response the server is allowed to
 * serve on the *next* request) and the trace footer's own code comment
 * describes a commit "popping in" on a poll. Neither one makes anything
 * happen on an *open* tab — there was no client-side timer anywhere, so a
 * page left open showed a screenshot, not a dashboard, for as long as
 * anyone sat on it. On a product whose Overview literally prints
 * "Recomputed just now," that is the one inconsistency that undercuts the
 * rest of the claim.
 *
 * `router.refresh()` re-runs the server components for the current route
 * with fresh data and preserves client state (scroll position, open
 * dropdowns) — it is not a reload. Paused when the tab is hidden (no point
 * spending a request nobody sees) and skipped entirely under
 * `prefers-reduced-motion`, since the memory trace's own new-commit
 * highlight is a motion effect and this is what triggers it.
 */
const INTERVAL_MS = 12_000;

export function LiveRefresh() {
  const router = useRouter();

  React.useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    let id: ReturnType<typeof setInterval> | null = null;

    function start() {
      if (id !== null) return;
      id = setInterval(() => {
        if (document.visibilityState === "visible") router.refresh();
      }, INTERVAL_MS);
    }
    function stop() {
      if (id === null) return;
      clearInterval(id);
      id = null;
    }

    function onVisibilityChange() {
      if (document.visibilityState === "visible") start();
    }

    start();
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [router]);

  return null;
}
