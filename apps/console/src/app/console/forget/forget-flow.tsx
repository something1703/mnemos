"use client";

import * as React from "react";
import { CheckCircle2, Gavel, Loader2 } from "lucide-react";
import { Button, Card, CardBody, CardHeader, CardTitle } from "@/components/ui/primitives";
import { AdminKeyField, ConfirmGate, callAdmin } from "@/components/admin-gate";

type Mode = "redact" | "forget" | "quarantine" | "shred";

/**
 * Plain-language consequences, per mode (Phase 08.2).
 *
 * Written so someone who has never read the schema can tell these apart. The
 * reversible one says so; the tenant-wide one says so in the loudest place,
 * because "shred" sounds narrower than it is and that misreading would be
 * catastrophic exactly once.
 */
const MODES: Record<Mode, { title: string; body: string; reversible: boolean; scope: string }> = {
  quarantine: {
    title: "Quarantine",
    body: "Keeps everything, but withdraws it from recall. The agent stops seeing it; the record stays intact for an auditor.",
    reversible: true,
    scope: "This subject",
  },
  redact: {
    title: "Redact",
    body: "Replaces the content with a tombstone and keeps the row and its audit history. You can still prove what existed and when it was removed.",
    reversible: false,
    scope: "This subject",
  },
  forget: {
    title: "Forget",
    body: "Deletes episodes, facts, vectors and provenance atomically. The audit chain still records that the erasure happened.",
    reversible: false,
    scope: "This subject",
  },
  shred: {
    title: "Shred",
    body: "Forget, plus destroys the tenant's encryption key so backup and MVCC copies become unreadable too. Every other subject in this tenant becomes unreadable as well.",
    reversible: false,
    scope: "THE WHOLE TENANT",
  },
};

interface Preview {
  preview: boolean;
  mode: string;
  would_remove: Record<string, number>;
  next?: string;
}

export function ForgetFlow({
  activeHolds,
}: {
  activeHolds: { subject_key: string; matter_reference: string }[];
}) {
  const [subject, setSubject] = React.useState("");
  const [mode, setMode] = React.useState<Mode>("quarantine");
  const [adminKey, setAdminKey] = React.useState("");
  const [reason, setReason] = React.useState("");
  const [preview, setPreview] = React.useState<Preview | null>(null);
  const [done, setDone] = React.useState<Record<string, unknown> | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState(false);

  // The hold check happens here, before anything is previewed or pressed.
  const hold = activeHolds.find((h) => h.subject_key === subject.trim());

  async function runPreview() {
    setError(null);
    setDone(null);
    setPending(true);
    try {
      const result = (await callAdmin(adminKey, "forget", {
        subject_key: subject.trim(),
        reason: reason || "preview",
        mode,
        confirm: false,
      })) as Preview;
      setPreview(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "preview failed");
      setPreview(null);
    } finally {
      setPending(false);
    }
  }

  async function execute() {
    setError(null);
    setPending(true);
    try {
      const result = (await callAdmin(adminKey, "forget", {
        subject_key: subject.trim(),
        reason,
        mode,
        confirm: true,
      })) as Record<string, unknown>;
      setDone(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "the erasure was refused");
    } finally {
      setPending(false);
    }
  }

  const chosen = MODES[mode];

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader>
          <CardTitle>Subject</CardTitle>
        </CardHeader>
        <CardBody className="flex flex-col gap-4">
          <input
            value={subject}
            onChange={(event) => {
              setSubject(event.target.value);
              setPreview(null);
              setDone(null);
            }}
            placeholder="subject_key, e.g. patient:1234"
            aria-label="Subject key"
            className="h-11 w-full rounded border border-hairline bg-veil px-3 font-mono text-sm text-parchment placeholder:font-body placeholder:text-dim"
          />

          {/* Surfaced before the button, not after. */}
          {hold ? (
            <div className="flex flex-col gap-1 rounded-lg border border-ember-edge bg-ember-fill p-4">
              <p className="flex items-center gap-2 text-sm font-medium text-ember">
                <Gavel className="size-4" aria-hidden />
                This subject is under a legal hold
              </p>
              <p className="text-sm text-moonstone">
                Matter <span className="font-mono text-parchment">{hold.matter_reference}</span>.
                Erasure will be refused and the refusal recorded. A system that always deletes on
                request is not compliant, only obedient.
              </p>
            </div>
          ) : null}

          <fieldset className="flex flex-col gap-2">
            <legend className="pb-1 text-xs font-medium tracking-wide text-moonstone uppercase">
              Mode
            </legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {(Object.keys(MODES) as Mode[]).map((key) => {
                const info = MODES[key];
                const selected = mode === key;
                return (
                  <label
                    key={key}
                    className={`flex cursor-pointer flex-col gap-1 rounded-lg border p-3 transition-colors ${
                      selected
                        ? key === "shred"
                          ? "border-signal-edge bg-signal-fill"
                          : "border-parchment/40 bg-veil-hi"
                        : "border-hairline bg-veil-hi/40 hover:border-moonstone/40"
                    }`}
                  >
                    <span className="flex items-center gap-2">
                      <input
                        type="radio"
                        name="mode"
                        checked={selected}
                        onChange={() => {
                          setMode(key);
                          setPreview(null);
                          setDone(null);
                        }}
                        className="accent-[var(--synapse)]"
                      />
                      <span className="font-display text-sm text-parchment">{info.title}</span>
                      {info.reversible ? (
                        <span className="rounded-sm border border-synapse-edge px-1.5 text-xs text-synapse">
                          reversible
                        </span>
                      ) : null}
                      {key === "shred" ? (
                        <span className="ml-auto rounded-sm border border-signal-edge bg-signal-fill px-1.5 text-xs font-semibold text-signal">
                          {info.scope}
                        </span>
                      ) : null}
                    </span>
                    <span className="text-xs text-moonstone">{info.body}</span>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <AdminKeyField value={adminKey} onChange={setAdminKey} />

          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium tracking-wide text-moonstone uppercase">
              Reason (recorded in the audit row)
            </span>
            <input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="e.g. GDPR Art. 17 request #4821"
              className="h-10 w-full rounded border border-hairline bg-veil px-3 text-sm text-parchment placeholder:text-dim"
            />
          </label>

          <Button
            variant="primary"
            onClick={runPreview}
            disabled={!subject.trim() || !adminKey || pending}
            className="w-fit"
          >
            {pending && !done ? "Previewing…" : "Preview what dies"}
          </Button>
        </CardBody>
      </Card>

      {error ? (
        <Card className="border-signal-edge bg-signal-fill">
          <CardBody className="py-3">
            <p className="text-sm font-medium text-signal">Refused</p>
            <p className="mt-1 font-mono text-xs break-all text-moonstone">{error}</p>
          </CardBody>
        </Card>
      ) : null}

      {preview && !done ? (
        <Card>
          <CardHeader>
            <CardTitle>Exactly what {chosen.title.toLowerCase()} would do</CardTitle>
            <span className="text-xs text-moonstone">nothing has happened yet</span>
          </CardHeader>
          <CardBody className="flex flex-col gap-4">
            <div className="grid gap-3 sm:grid-cols-3">
              {Object.entries(preview.would_remove ?? {}).map(([what, count]) => (
                <div key={what} className="rounded-lg border border-hairline bg-veil-hi px-3 py-2">
                  <p className="tabular font-display text-2xl text-signal">{count}</p>
                  <p className="text-xs text-moonstone">{what.replace(/_/g, " ")}</p>
                </div>
              ))}
            </div>

            <ConfirmGate
              phrase={subject.trim()}
              label={`${chosen.title} subject`}
              pending={pending}
              disabled={!reason}
              onConfirm={execute}
            >
              <p className="text-sm text-moonstone">
                {chosen.body}{" "}
                {mode === "shred" ? (
                  <span className="font-semibold text-signal">
                    This affects every subject in the tenant, not only this one.
                  </span>
                ) : null}
              </p>
              {!reason ? (
                <p className="text-xs text-ember">A reason is required before this can execute.</p>
              ) : null}
            </ConfirmGate>
          </CardBody>
        </Card>
      ) : null}

      {done ? <Proof subject={subject.trim()} mode={chosen.title} result={done} /> : null}
    </div>
  );
}

/**
 * The proof screen. An erasure that cannot be proven is indistinguishable
 * from one that did not happen, so this shows the receipt rather than a toast.
 */
function Proof({
  subject,
  mode,
  result,
}: {
  subject: string;
  mode: string;
  result: Record<string, unknown>;
}) {
  const [verify, setVerify] = React.useState<{ valid?: boolean; entries_checked?: number } | null>(
    null,
  );
  const [recallEmpty, setRecallEmpty] = React.useState<number | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    // Re-verify the chain and re-run recall AFTER the erasure — the two claims
    // that make this a proof rather than a confirmation message.
    void fetch("/api/proof?subject=" + encodeURIComponent(subject))
      .then((r) => r.json())
      .then((body) => {
        if (cancelled) return;
        setVerify(body.verify ?? null);
        setRecallEmpty(body.recalled ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [subject]);

  return (
    <Card className="border-synapse-edge">
      <CardHeader>
        <CardTitle>
          <span className="flex items-center gap-2">
            <CheckCircle2 className="size-4 text-synapse" aria-hidden />
            {mode} complete — proof
          </span>
        </CardTitle>
      </CardHeader>
      <CardBody className="flex flex-col gap-3 text-sm">
        <pre className="overflow-x-auto rounded border border-hairline bg-abyss p-3 font-mono text-xs text-moonstone">
          {JSON.stringify(result, null, 2)}
        </pre>

        <div className="flex items-center gap-3">
          <span className="text-moonstone">Chain re-verified</span>
          {verify === null ? (
            <Loader2 className="size-4 animate-spin text-dim" aria-hidden />
          ) : (
            <span
              className={
                verify.valid
                  ? "rounded-sm border border-synapse-edge bg-synapse-fill px-2 py-0.5 text-xs font-semibold text-synapse"
                  : "rounded-sm border border-signal-edge bg-signal-fill px-2 py-0.5 text-xs font-semibold text-signal"
              }
            >
              {verify.valid ? "VALID" : "BROKEN"}
            </span>
          )}
          {verify?.entries_checked ? (
            <span className="text-xs text-moonstone">over {verify.entries_checked} entries</span>
          ) : null}
        </div>

        <div className="flex items-center gap-3">
          <span className="text-moonstone">Recall for this subject</span>
          {recallEmpty === null ? (
            <Loader2 className="size-4 animate-spin text-dim" aria-hidden />
          ) : (
            <span className={recallEmpty === 0 ? "text-synapse" : "text-ember"}>
              {recallEmpty === 0
                ? "returns nothing"
                : `still returns ${recallEmpty} fact(s) — expected for reversible modes`}
            </span>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
