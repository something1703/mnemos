"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { FileSearch } from "lucide-react";
import { Button } from "@/components/ui/primitives";

export function ActionForm({ actionId }: { actionId: string }) {
  const router = useRouter();
  const [value, setValue] = useState(actionId);
  const [pending, startTransition] = useTransition();

  return (
    <form
      className="flex gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        const next = value.trim();
        startTransition(() =>
          router.push(next ? `/console/deposition?action=${encodeURIComponent(next)}` : "/console/deposition"),
        );
      }}
    >
      <div className="relative flex-1">
        <FileSearch
          className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-dim"
          aria-hidden
        />
        <input
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Action id (uuid)"
          aria-label="Action id"
          className="h-11 w-full rounded border border-hairline bg-veil pr-3 pl-9 font-mono text-sm text-parchment placeholder:font-body placeholder:text-dim"
        />
      </div>
      <Button type="submit" variant="primary" size="lg" disabled={pending}>
        {pending ? "Building…" : "Depose"}
      </Button>
    </form>
  );
}
