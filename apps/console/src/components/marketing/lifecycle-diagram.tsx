"use client";

import * as React from "react";
import {
  ReactFlow,
  Handle,
  Position,
  MarkerType,
  type Node,
  type Edge,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useReducedMotion } from "motion/react";
import { cn } from "@/lib/utils";

const ACCENT_VAR: Record<string, string> = {
  ember: "var(--ember)",
  synapse: "var(--synapse)",
  signal: "var(--signal)",
  umbra: "var(--umbra)",
  parchment: "var(--moonstone)",
};

type StageId = "remember" | "sleepCycle" | "facts" | "ledger" | "warden" | "recall";
type ElementState = "dim" | "current" | "lit";

interface StageData {
  title: string;
  detail: string;
  accent: "ember" | "synapse" | "signal" | "umbra" | "parchment";
  llm?: boolean;
  /** Only `remember()` and `recall()` are real function identifiers; "sleep
   * cycle", "the ledger", "the Warden", and "semantic facts" are
   * human-written plane names. The Mono-Is-Semantic rule (DESIGN.md) says
   * mono marks a machine-authored, verifiable value — applying it uniformly
   * to both kinds was exactly the violation this flag exists to prevent. */
  mono?: boolean;
  wide?: boolean;
  state: ElementState;
  [key: string]: unknown;
}

// order: the step at which this node lights up in the play-through sequence.
const STAGE_SPEC: Record<
  StageId,
  { title: string; detail: string; accent: StageData["accent"]; llm?: boolean; mono?: boolean; wide?: boolean; x: number; y: number; width: number; height: number; order: number }
> = {
  remember: {
    title: "remember()",
    detail: "an episode is written, encrypted, homed to a region — zero AI work",
    accent: "ember",
    mono: true,
    x: 0,
    y: 0,
    width: 190,
    height: 60,
    order: 0,
  },
  sleepCycle: {
    title: "sleep cycle",
    detail: "distills, corroborates, promotes — async, on a schedule",
    accent: "ember",
    llm: true,
    x: 260,
    y: 0,
    width: 190,
    height: 60,
    order: 2,
  },
  facts: {
    title: "semantic facts",
    detail: "trust starts at unverified and is earned from here",
    accent: "umbra",
    x: 520,
    y: 0,
    width: 190,
    height: 60,
    order: 4,
  },
  ledger: {
    title: "the ledger",
    detail: "every state change appends a hash-chained row, in the same transaction",
    accent: "parchment",
    wide: true,
    x: 220,
    y: 170,
    width: 300,
    height: 60,
    order: 3,
  },
  warden: {
    title: "the Warden",
    detail: "residency, legal hold, erasure, revocation — no model in this process",
    accent: "signal",
    x: 0,
    y: 300,
    width: 190,
    height: 60,
    order: 5,
  },
  recall: {
    title: "recall()",
    detail: "hybrid search, trust-gated, logged",
    accent: "synapse",
    mono: true,
    x: 520,
    y: 300,
    width: 190,
    height: 60,
    order: 6,
  },
};

const EDGE_SPEC: Array<{ id: string; source: StageId; target: StageId; label?: string; order: number }> = [
  { id: "e-remember-sleep", source: "remember", target: "sleepCycle", label: "async, later", order: 1 },
  { id: "e-sleep-facts", source: "sleepCycle", target: "facts", label: "distill, promote", order: 2 },
  { id: "e-facts-recall", source: "facts", target: "recall", label: "trust-gated", order: 4 },
  { id: "e-remember-ledger", source: "remember", target: "ledger", order: 3 },
  { id: "e-sleep-ledger", source: "sleepCycle", target: "ledger", order: 3 },
  { id: "e-warden-ledger", source: "warden", target: "ledger", order: 5 },
  { id: "e-recall-ledger", source: "recall", target: "ledger", order: 6 },
];

const TOTAL_STEPS = 7;
const HOLD_MS = 550;
const LONG_HOLD_MS = 1300;
const LONG_HOLD_AFTER = new Set([3, 6]); // the ledger's first commit, and the final one

function stateOf(order: number, stepIndex: number): ElementState {
  if (stepIndex < 0 || order > stepIndex) return "dim";
  if (order === stepIndex) return "current";
  return "lit";
}

const HANDLE_STYLE: React.CSSProperties = { opacity: 0, width: 1, height: 1, minWidth: 1, minHeight: 1 };

function StageNode({ data }: NodeProps<Node<StageData>>) {
  return (
    <div
      className={cn(
        "flex flex-col gap-1 rounded-lg border bg-veil px-4 py-3 transition-[border-color,opacity] duration-300",
        data.wide ? "items-center text-center" : "",
        data.state === "dim" ? "border-hairline opacity-45" : "border-hairline opacity-100",
      )}
      style={{ width: data.wide ? 300 : 190 }}
    >
      <Handle type="target" position={Position.Top} style={HANDLE_STYLE} />
      <Handle type="source" position={Position.Bottom} style={HANDLE_STYLE} />
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
      <div className={cn("flex items-center gap-2", data.wide && "justify-center")}>
        <span
          className="size-2 shrink-0 rounded-full transition-transform duration-300"
          style={{
            backgroundColor: ACCENT_VAR[data.accent],
            transform: data.state === "current" ? "scale(1.3)" : "scale(1)",
            filter: data.state === "current" ? `drop-shadow(0 0 6px ${ACCENT_VAR[data.accent]})` : undefined,
          }}
          aria-hidden
        />
        <span className={cn("text-sm text-parchment", data.mono ? "font-mono" : "font-display")}>
          {data.title}
        </span>
        {data.llm ? (
          <span className="rounded-sm border border-hairline px-1.5 py-px text-xs tracking-wide text-moonstone uppercase">
            model
          </span>
        ) : null}
      </div>
      <p className="text-xs text-moonstone">{data.detail}</p>
    </div>
  );
}

const NODE_TYPES = { stage: StageNode };

/**
 * What actually happens between `remember()` and `recall()`, drawn and
 * routed by the same graph library the trust lattice uses — a real pipeline
 * rather than a generic "AI memory" graphic. Every node and edge is the real
 * shape `packages/engine`, `services/sleep-cycle`, and `packages/warden`
 * are: the ledger sits as a genuine hub with five real incoming edges,
 * because that is what "one transactionally consistent database" actually
 * looks like as a graph, not a linear write-path-then-read-path cartoon.
 *
 * Plays through once on scroll-into-view, each stage lighting up in the
 * order data actually flows, then holds on the fully-lit state — the same
 * one-time-sequence contract every other diagram on this site keeps, so
 * continuous looping motion stays reserved for the memory trace alone.
 */
export function LifecycleDiagram() {
  const reduced = useReducedMotion();
  const [stepIndex, setStepIndex] = React.useState(reduced ? TOTAL_STEPS - 1 : -1);
  const [started, setStarted] = React.useState(reduced);
  const wrapperRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (started) return;
    const el = wrapperRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setStarted(true);
          observer.disconnect();
        }
      },
      { threshold: 0.4 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [started]);

  React.useEffect(() => {
    if (!started || reduced) return;
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout>;

    function advance(i: number) {
      if (cancelled) return;
      setStepIndex(i);
      if (i < 0) {
        timeoutId = setTimeout(() => advance(0), HOLD_MS);
        return;
      }
      if (i >= TOTAL_STEPS - 1) return;
      const delay = LONG_HOLD_AFTER.has(i) ? LONG_HOLD_MS : HOLD_MS;
      timeoutId = setTimeout(() => advance(i + 1), delay);
    }

    advance(-1);
    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [started, reduced]);

  const nodes: Node<StageData>[] = React.useMemo(
    () =>
      (Object.entries(STAGE_SPEC) as [StageId, (typeof STAGE_SPEC)[StageId]][]).map(([id, spec]) => ({
        id,
        type: "stage",
        position: { x: spec.x, y: spec.y },
        width: spec.width,
        height: spec.height,
        draggable: false,
        selectable: false,
        connectable: false,
        data: {
          title: spec.title,
          detail: spec.detail,
          accent: spec.accent,
          llm: spec.llm,
          mono: spec.mono,
          wide: spec.wide,
          state: stateOf(spec.order, stepIndex),
        },
      })),
    [stepIndex],
  );

  const edges: Edge[] = React.useMemo(
    () =>
      EDGE_SPEC.map((spec) => {
        const state = stateOf(spec.order, stepIndex);
        const colour = state === "dim" ? "var(--hairline)" : ACCENT_VAR[STAGE_SPEC[spec.source].accent];
        return {
          id: spec.id,
          source: spec.source,
          target: spec.target,
          type: "smoothstep",
          animated: !reduced && state === "current",
          selectable: false,
          focusable: false,
          markerEnd: state === "dim" ? undefined : { type: MarkerType.ArrowClosed, color: colour, width: 14, height: 14 },
          label: spec.label,
          labelStyle: { fill: "var(--moonstone)", fontSize: 11, opacity: state === "dim" ? 0.5 : 1 },
          labelBgStyle: { fill: "var(--veil)", fillOpacity: 1, stroke: "var(--hairline)", strokeWidth: 1 },
          labelBgPadding: [5, 3] as [number, number],
          labelBgBorderRadius: 4,
          style: { stroke: colour, strokeWidth: 1.5, opacity: state === "dim" ? 0.35 : 1 },
        } satisfies Edge;
      }),
    [stepIndex, reduced],
  );

  const onInit = React.useCallback((instance: ReactFlowInstance<Node<StageData>, Edge>) => {
    instance.fitView({ padding: 0.1 });
  }, []);

  return (
    <div ref={wrapperRef} className="flex flex-col gap-3">
      <div
        className="h-[280px] w-full md:h-[320px]"
        role="img"
        aria-label="The memory lifecycle: remember() writes an episode, the sleep cycle distills it into semantic facts asynchronously, recall() reads trust-gated facts, and every stage including the Warden appends to the ledger."
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.1 }}
          onInit={onInit}
          minZoom={0.4}
          maxZoom={1}
          panOnDrag={false}
          panOnScroll={false}
          zoomOnScroll={false}
          zoomOnPinch={false}
          zoomOnDoubleClick={false}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          proOptions={{ hideAttribution: true }}
        />
      </div>
      <p className="mx-auto max-w-md text-center text-xs text-moonstone">
        Every stage above appends to the ledger on every state change — the Warden included.
        Nothing skips it.
      </p>
    </div>
  );
}
