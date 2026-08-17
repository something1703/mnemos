"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Search } from "lucide-react";
import { Button } from "@/components/ui/primitives";

/**
 * A plain form that writes the query into the URL. `useTransition` keeps the
 * previous results on screen while the server component re-renders, so the
 * page never flashes empty between searches — the closest thing to "live"
 * recall without giving the browser a key of its own.
 */
export function SearchForm({
  query,
  includeUnverified,
}: {
  query: string;
  includeUnverified: boolean;
}) {
  const router = useRouter();
  const [value, setValue] = useState(query);
  const [unverified, setUnverified] = useState(includeUnverified);
  const [pending, startTransition] = useTransition();

  function submit(nextUnverified = unverified) {
    const params = new URLSearchParams();
    if (value.trim()) params.set("q", value.trim());
    if (nextUnverified) params.set("unverified", "1");
    startTransition(() => router.push(`/console/explorer?${params.toString()}`));
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      role="search"
    >
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-dim"
            aria-hidden
          />
          <input
            type="search"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="What would an agent ask?"
            aria-label="Recall query"
            className="h-11 w-full rounded border border-hairline bg-veil pr-3 pl-9 text-sm text-parchment placeholder:text-dim"
          />
        </div>
        <Button type="submit" variant="primary" size="lg" disabled={pending}>
          {pending ? "Recalling…" : "Recall"}
        </Button>
      </div>

      <label className="flex w-fit cursor-pointer items-center gap-2 text-xs text-moonstone">
        <input
          type="checkbox"
          checked={unverified}
          onChange={(event) => {
            setUnverified(event.target.checked);
            submit(event.target.checked);
          }}
          className="size-3.5 accent-[var(--umbra)]"
        />
        Include unverified facts
        <span className="text-moonstone">— what the agent would normally not be shown</span>
      </label>
    </form>
  );
}
