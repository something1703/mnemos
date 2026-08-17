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
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { motion, useReducedMotion } from "motion/react";
import { TrustBadge } from "@/components/ui/trust-badge";
import { cn } from "@/lib/utils";
import { TRUST_PRESENTATION, type TrustState } from "@/lib/brand";

const ACCENT_VAR: Record<string, string> = {
  synapse: "var(--synapse)",
  ember: "var(--ember)",
  signal: "var(--signal)",
  umbra: "var(--umbra)",
  parchment: "var(--parchment)",
};

/**
 * How a claim actually earns trust — the same five states `TrustBadge` draws
 * everywhere in the console, laid out and routed by a real graph library
 * (`@xyflow/react`) as the real promotion paths
 * (`packages/engine/src/mnemos_engine/corroboration.py`), not a generic
 * funnel graphic. Every node and edge here is a real code path, not an
 * illustration invented for the page — no edge connects states that cannot
 * actually reach each other.
 *
 * Plays as a sequence rather than fading in all at once — each node lights
 * up in promotion order, once, and the edge it just crossed flows (an
 * animated dash, react-flow's own primitive for "something is moving along
 * this path") for exactly the moment it is current, then settles to a solid,
 * lit line. A viewer should be able to read the story by watching, not by
 * decoding a static graph. Does not loop, and no edge keeps flowing once its
 * step has passed: BRAND.md reserves continuous, repeating motion for the
 * memory trace alone. Starts already fully lit under `prefers-reduced-motion`.
 * Panning, zooming, and dragging are all disabled — this is an illustration
 * that borrows react-flow's routing and flow-animation, not an interactive
 * canvas widget dropped onto a marketing page.
 */

type NodeId = "unverified" | "corroborated" | "trusted" | "contested" | "quarantined";
type ElementState = "dim" | "current" | "lit";

interface LatticeNodeData {
  trust: TrustState;
  note?: string;
  small?: boolean;
  state: ElementState;
  [key: string]: unknown;
}

// order: the step index (into the flat 8-step timeline) at which this node
// lights up. edges reference the step index at which THEY are the current
// (animated) transition.
const NODE_SPEC: Record<NodeId, { trust: TrustState; note?: string; small?: boolean; x: number; y: number; order: number }> = {
  unverified: { trust: "unverified", note: "everything an LLM writes lands here", x: 0, y: 40, order: 0 },
  corroborated: { trust: "corroborated", x: 300, y: 40, order: 2 },
  trusted: { trust: "trusted", note: "returned by recall", x: 600, y: 40, order: 4 },
  contested: { trust: "contested", note: "a comparable claim disagrees", x: 150, y: 220, small: true, order: 5 },
  quarantined: { trust: "quarantined", note: "withdrawn from recall", x: 450, y: 220, small: true, order: 7 },
};

const EDGE_SPEC: Array<{ id: string; source: NodeId; target: NodeId; label: string; order: number }> = [
  { id: "e-unverified-corroborated", source: "unverified", target: "corroborated", label: "2 independent sources", order: 1 },
  { id: "e-corroborated-trusted", source: "corroborated", target: "trusted", label: "system / operator source", order: 3 },
  { id: "e-contested-quarantined", source: "contested", target: "quarantined", label: "source revoked or gone stale", order: 6 },
];

const TOTAL_STEPS = 8;
const HOLD_MS = 650;
const LONG_HOLD_MS = 1500; // where the story pauses to let a completed path sink in
const LONG_HOLD_AFTER = new Set([4, 7]); // trusted, quarantined

function stateOf(order: number, stepIndex: number): ElementState {
  if (stepIndex < 0 || order > stepIndex) return "dim";
  if (order === stepIndex) return "current";
  return "lit";
}

const HANDLE_STYLE: React.CSSProperties = { opacity: 0, width: 1, height: 1, minWidth: 1, minHeight: 1 };

function LatticeNode({ data }: NodeProps<Node<LatticeNodeData>>) {
  const glow = ACCENT_VAR[TRUST_PRESENTATION[data.trust].accent];
  return (
    <div className="flex flex-col items-center gap-1.5">
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <motion.div
        animate={
          data.state === "current"
            ? { scale: [1, 1.16, 1], opacity: 1 }
            : { scale: 1, opacity: data.state === "dim" ? 0.32 : 1 }
        }
        transition={data.state === "current" ? { duration: 0.5, ease: "easeOut" } : { duration: 0.35 }}
        style={data.state === "current" ? { filter: `drop-shadow(0 0 10px ${glow})` } : undefined}
      >
        <TrustBadge trust={data.trust} className={data.small ? "opacity-90" : "text-sm"} />
      </motion.div>
      {data.note ? (
        <span
          className={cn(
            "max-w-[15ch] text-center text-xs transition-colors duration-300",
            data.state === "dim" ? "text-moonstone/50" : "text-moonstone",
          )}
        >
          {data.note}
        </span>
      ) : null}
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
    </div>
  );
}

const NODE_TYPES = { lattice: LatticeNode };

export function TrustLatticeDiagram() {
  const reduced = useReducedMotion();
  const [stepIndex, setStepIndex] = React.useState(reduced ? TOTAL_STEPS - 1 : -1);

  React.useEffect(() => {
    if (reduced) return; // frozen on the fully-lit state, set above
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout>;

    function advance(i: number) {
      if (cancelled) return;
      setStepIndex(i);
      if (i < 0) {
        timeoutId = setTimeout(() => advance(0), HOLD_MS);
        return;
      }
      if (i >= TOTAL_STEPS - 1) {
        // Plays through once, then holds — BRAND.md reserves continuous,
        // looping motion for the memory trace alone.
        return;
      }
      const delay = LONG_HOLD_AFTER.has(i) ? LONG_HOLD_MS : HOLD_MS;
      timeoutId = setTimeout(() => advance(i + 1), delay);
    }

    advance(-1);
    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [reduced]);

  const nodes: Node<LatticeNodeData>[] = React.useMemo(
    () =>
      (Object.entries(NODE_SPEC) as [NodeId, (typeof NODE_SPEC)[NodeId]][]).map(([id, spec]) => ({
        id,
        type: "lattice",
        position: { x: spec.x, y: spec.y },
        draggable: false,
        selectable: false,
        connectable: false,
        data: {
          trust: spec.trust,
          note: spec.note,
          small: spec.small,
          state: stateOf(spec.order, stepIndex),
        },
      })),
    [stepIndex],
  );

  const edges: Edge[] = React.useMemo(
    () =>
      EDGE_SPEC.map((spec) => {
        const state = stateOf(spec.order, stepIndex);
        const accent = ACCENT_VAR[TRUST_PRESENTATION[NODE_SPEC[spec.target].trust].accent];
        const colour = state === "dim" ? "var(--hairline)" : accent;
        return {
          id: spec.id,
          source: spec.source,
          target: spec.target,
          type: "straight",
          animated: !reduced && state === "current",
          selectable: false,
          focusable: false,
          markerEnd: state === "dim" ? undefined : { type: MarkerType.ArrowClosed, color: colour, width: 14, height: 14 },
          label: spec.label,
          labelStyle: { fill: state === "dim" ? "var(--moonstone)" : "var(--moonstone)", fontSize: 11, opacity: state === "dim" ? 0.5 : 1 },
          labelBgStyle: { fill: "var(--abyss)", fillOpacity: 0.85 },
          labelBgPadding: [4, 2] as [number, number],
          labelBgBorderRadius: 3,
          style: { stroke: colour, strokeWidth: 1.5, opacity: state === "dim" ? 0.4 : 1 },
        } satisfies Edge;
      }),
    [stepIndex, reduced],
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="h-[300px] w-full md:h-[280px]" aria-hidden={false} role="img" aria-label="The trust lattice: how a claim moves from unverified to corroborated to trusted, or from contested to quarantined">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.15 }}
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
        Promotion needs a <em>different session</em> and a <em>different source
        origin</em> — the same repeated claim from one channel never counts twice.
      </p>
    </div>
  );
}
