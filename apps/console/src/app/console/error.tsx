"use client";

import { AlertTriangle } from "lucide-react";
import { Button, EmptyState } from "@/components/ui/primitives";

/**
 * Without this, an unhandled render error drops a visitor onto Next's
 * generic error page — on a product whose thesis is honest disclosure, the
 * console's own screens should be the ones saying so, in the same voice
 * everything else here uses.
 */
export default function ConsoleError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <EmptyState
      title="This screen failed to render"
      action={
        <Button variant="default" size="sm" onClick={reset}>
          Try again
        </Button>
      }
    >
      <span className="flex items-center gap-2 text-signal">
        <AlertTriangle className="size-4" aria-hidden />
        {error.message || "An unexpected error occurred."}
      </span>
      {error.digest ? (
        <span className="mt-1 block font-mono text-xs text-moonstone">digest: {error.digest}</span>
      ) : null}
    </EmptyState>
  );
}
