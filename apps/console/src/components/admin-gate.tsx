"use client";

import * as React from "react";
import { KeyRound, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/primitives";

/**
 * Admin key entry, and the type-to-confirm gate.
 *
 * The key is component state and nothing else. No localStorage, no
 * sessionStorage, no cookie — Phase 08.3 is explicit, and a governance console
 * that quietly persists an admin credential would undermine the dual-control
 * story it exists to demonstrate. Navigating away loses it, which is correct.
 *
 * `autoComplete="off"` plus `type="password"` keeps browsers from offering to
 * save it, which is the other way a key ends up persisted without anyone
 * deciding to.
 */
export function AdminKeyField({
  value,
  onChange,
  label = "Admin key",
}: {
  value: string;
  onChange: (next: string) => void;
  label?: string;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="flex items-center gap-2 text-xs font-medium tracking-wide text-dim uppercase">
        <KeyRound className="size-3.5" aria-hidden />
        {label}
      </span>
      <input
        type="password"
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="mn_live_…"
        className="h-10 w-full rounded border border-hairline bg-veil px-3 font-mono text-sm text-parchment placeholder:font-body placeholder:text-dim"
      />
      <span className="text-xs text-dim">
        Used for this one call and never stored — not in local storage, not in a cookie. Leaving
        this page discards it.
      </span>
    </label>
  );
}

/**
 * Type-to-confirm. The user must reproduce `phrase` exactly.
 *
 * Signal red appears on the button here and nowhere else in the console
 * (BRAND.md), which is what makes it mean "something is being destroyed"
 * rather than "this button is important".
 */
export function ConfirmGate({
  phrase,
  label,
  pending,
  disabled,
  onConfirm,
  children,
}: {
  phrase: string;
  label: string;
  pending?: boolean;
  disabled?: boolean;
  onConfirm: () => void;
  children?: React.ReactNode;
}) {
  const [typed, setTyped] = React.useState("");
  const matches = typed === phrase;

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-signal-edge bg-signal-fill p-4">
      <p className="flex items-center gap-2 text-sm font-medium text-signal">
        <ShieldAlert className="size-4" aria-hidden />
        This cannot be undone
      </p>
      {children}
      <label className="flex flex-col gap-1.5">
        <span className="text-xs text-moonstone">
          Type <span className="font-mono text-parchment">{phrase}</span> to confirm
        </span>
        <input
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          autoComplete="off"
          spellCheck={false}
          aria-label={`Type ${phrase} to confirm`}
          className="h-10 w-full rounded border border-hairline bg-veil px-3 font-mono text-sm text-parchment"
        />
      </label>
      <Button
        variant="destroy"
        size="lg"
        disabled={!matches || pending || disabled}
        onClick={onConfirm}
        className="w-fit"
      >
        {pending ? "Executing…" : label}
      </Button>
    </div>
  );
}

/** POST to the admin route. Returns the tool result, or throws with the
 * Warden's own refusal message intact. */
export async function callAdmin(
  key: string,
  tool: string,
  args: Record<string, unknown>,
): Promise<unknown> {
  const response = await fetch("/api/admin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, tool, args }),
  });
  const body = await response.json();
  if (!response.ok || body.ok === false) {
    throw new Error(body.error ?? "the operation failed");
  }
  return body.result;
}
