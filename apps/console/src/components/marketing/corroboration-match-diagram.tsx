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
import { TrustBadge } from "@/components/ui/trust-badge";
import { cn } from "@/lib/utils";

type NodeId = "sessionA" | "sessionB" | "sessionC" | "agent" | "external" | "result";
type Phase = "idle" | "first" | "redundant" | "second" | "resolved";

interface PillData {
  label: string;
  sub?: string;
  kind: "session" | "category";
  lit: boolean;
  [key: string]: unknown;
}

interface ResultData {
  visible: boolean;
  [key: string]: unknown;
}

const NODE_POS: Record<NodeId, { x: number; y: number; width: number; height: number }> = {
  sessionA: { x: 0, y: 0, width: 128, height: 34 },
  sessionB: { x: 0, y: 66, width: 128, height: 34 },
  sessionC: { x: 0, y: 132, width: 128, height: 34 },
  agent: { x: 320, y: 20, width: 128, height: 34 },
  external: { x: 320, y: 112, width: 128, height: 34 },
  result: { x: 520, y: 66, width: 150, height: 34 },
};

// Which phase (see PHASES below) makes each edge "current", i.e. animated
// and freshly drawn. Everything at or before the active phase renders lit;
// everything after stays dim.
const EDGE_PHASE: Record<string, Phase> = {
  "sessionA-agent": "first",
  "sessionB-agent": "redundant",
  "sessionC-external": "second",
  "agent-result": "resolved",
  "external-result": "resolved",
};

const PHASES: Phase[] = ["idle", "first", "redundant", "second", "resolved"];
const HOLD_MS = 750;
const RESOLVED_HOLD_MS = 1600;

const HANDLE_STYLE: React.CSSProperties = { opacity: 0, width: 1, height: 1, minWidth: 1, minHeight: 1 };

function PillNode({ data }: NodeProps<Node<PillData>>) {
  return (
    <div
      className={cn(
        "flex h-full items-center justify-center rounded-md border px-3 text-center text-xs font-medium transition-[opacity,border-color,color] duration-300",
        data.kind === "session" ? "font-mono" : "font-display",
        data.lit
          ? "border-synapse-edge bg-synapse-fill text-synapse"
          : "border-hairline bg-veil text-moonstone opacity-70",
      )}
    >
      <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      {data.label}
    </div>
  );
}

function ResultNode({ data }: NodeProps<Node<ResultData>>) {
  return (
    <div
      className="flex h-full items-center justify-center transition-all duration-500"
      style={{
        opacity: data.visible ? 1 : 0,
        transform: data.visible ? "scale(1)" : "scale(0.6)",
        filter: data.visible ? "drop-shadow(0 0 8px var(--synapse))" : undefined,
      }}
    >
      <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />
      <TrustBadge trust="corroborated" />
    </div>
  );
}

const NODE_TYPES = { pill: PillNode, result: ResultNode };

/**
 * The mechanism the prose above describes — sessions on one side, source
 * categories on the other, an edge wherever a session contributed that
 * category — made visible instead of only asserted. Two sessions hitting the
 * same category (`sessionA`, `sessionB` → `agent`) draw a real second edge
 * that still does not open a second slot in the match; only a session
 * reaching a *different* category (`sessionC` → `external`) does. That is
 * exactly `corroboration.py`'s bipartite matching, not a simplified
 * illustration of it — real enough that "ten episodes, one channel, one
 * slot" is something a reader watches happen rather than takes on faith.
 */
export function CorroborationMatchDiagram() {
  const reduced = useReducedMotion();
  const [phaseIndex, setPhaseIndex] = React.useState(reduced ? PHASES.length - 1 : 0);
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
      setPhaseIndex(i);
      if (i >= PHASES.length - 1) return;
      const delay = PHASES[i] === "resolved" ? RESOLVED_HOLD_MS : HOLD_MS;
      timeoutId = setTimeout(() => advance(i + 1), delay);
    }
    advance(0);
    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [started, reduced]);

  const phase = PHASES[phaseIndex];
  const litCategories = React.useMemo(() => {
    const idx = PHASES.indexOf(phase);
    return {
      agent: idx >= PHASES.indexOf("first"),
      external: idx >= PHASES.indexOf("second"),
    };
  }, [phase]);

  const nodes: Node<PillData | ResultData>[] = React.useMemo(() => {
    const idx = PHASES.indexOf(phase);
    const sessionsLit = idx >= PHASES.indexOf("first");
    return [
      { id: "sessionA", type: "pill", position: NODE_POS.sessionA, ...NODE_POS.sessionA, draggable: false, selectable: false, data: { label: "session A", kind: "session", lit: sessionsLit } },
      { id: "sessionB", type: "pill", position: NODE_POS.sessionB, ...NODE_POS.sessionB, draggable: false, selectable: false, data: { label: "session B", kind: "session", lit: idx >= PHASES.indexOf("redundant") } },
      { id: "sessionC", type: "pill", position: NODE_POS.sessionC, ...NODE_POS.sessionC, draggable: false, selectable: false, data: { label: "session C", kind: "session", lit: idx >= PHASES.indexOf("second") } },
      { id: "agent", type: "pill", position: NODE_POS.agent, ...NODE_POS.agent, draggable: false, selectable: false, data: { label: "agent", kind: "category", lit: litCategories.agent } },
      { id: "external", type: "pill", position: NODE_POS.external, ...NODE_POS.external, draggable: false, selectable: false, data: { label: "external", kind: "category", lit: litCategories.external } },
      { id: "result", type: "result", position: NODE_POS.result, ...NODE_POS.result, draggable: false, selectable: false, data: { visible: idx >= PHASES.indexOf("resolved") } },
    ];
  }, [phase, litCategories]);

  const edges: Edge[] = React.useMemo(() => {
    const idx = PHASES.indexOf(phase);
    return Object.entries(EDGE_PHASE).map(([key, atPhase]) => {
      const [source, target] = key.split("-") as [NodeId, NodeId];
      const edgePhaseIdx = PHASES.indexOf(atPhase);
      const isRedundant = atPhase === "redundant";
      const visible = idx >= edgePhaseIdx;
      const current = idx === edgePhaseIdx;
      const colour = !visible ? "var(--hairline)" : isRedundant ? "var(--dim)" : "var(--synapse)";
      return {
        id: key,
        source,
        target,
        type: "straight",
        animated: !reduced && current,
        selectable: false,
        focusable: false,
        markerEnd: visible ? { type: MarkerType.ArrowClosed, color: colour, width: 12, height: 12 } : undefined,
        style: {
          stroke: colour,
          strokeWidth: isRedundant ? 1.25 : 1.5,
          strokeDasharray: isRedundant ? "3 3" : undefined,
          opacity: visible ? (isRedundant ? 0.7 : 1) : 0,
          transition: "opacity 300ms ease",
        },
      } satisfies Edge;
    });
  }, [phase, reduced]);

  const onInit = React.useCallback((instance: ReactFlowInstance<Node<PillData | ResultData>, Edge>) => {
    instance.fitView({ padding: 0.15 });
  }, []);

  return (
    <div ref={wrapperRef} className="flex flex-col gap-3">
      <div
        className="h-[190px] w-full md:h-[210px]"
        role="img"
        aria-label="Corroboration matching: session A and session B both point at the agent category, filling one slot; session C points at the external category, filling a second, independent slot — the fact promotes to corroborated only once both slots are filled."
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          onInit={onInit}
          minZoom={0.5}
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
      <p className="mx-auto max-w-sm text-center text-xs text-moonstone">
        Sessions A and B both land in <span className="text-dim">agent</span> — the second edge is
        real but fills no new slot. Session C reaching <span className="text-moonstone">external</span> is
        what actually promotes the fact.
      </p>
    </div>
  );
}
