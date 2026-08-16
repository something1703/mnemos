"use client";

import * as React from "react";
import { scaleLinear, scaleUtc } from "d3-scale";
import { area, line, curveMonotoneX } from "d3-shape";
import { motion, useReducedMotion } from "motion/react";
import { cn, formatCount } from "@/lib/utils";

export interface Bucket {
  /** ISO timestamp — plain strings cross the server/client boundary without
   * any ambiguity about serialization, and every bucket is built fresh from
   * real `committed_at` values rather than carried across as a Date. */
  at: string;
  count: number;
}

interface PlottedBucket {
  at: Date;
  count: number;
}

/**
 * Real ledger commits, bucketed by hour and drawn as one series — a line
 * chart with an area wash under it (dataviz mark spec: 2px line, ~10% fill),
 * not a fabricated trend. One series needs no legend; the card title says
 * what is plotted, and the synapse hue is the same "reading/live-data" accent
 * every other live number in the console already uses.
 */
export function ActivitySparkline({
  buckets,
  className,
  height = 120,
}: {
  buckets: Bucket[];
  className?: string;
  height?: number;
}) {
  const reduced = useReducedMotion();
  const [hoverIndex, setHoverIndex] = React.useState<number | null>(null);
  const svgRef = React.useRef<SVGSVGElement>(null);

  const plotted = React.useMemo<PlottedBucket[]>(
    () => buckets.map((b) => ({ at: new Date(b.at), count: b.count })),
    [buckets],
  );

  const width = 480;
  const margin = { top: 8, right: 4, bottom: 18, left: 4 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const x = React.useMemo(
    () =>
      scaleUtc()
        .domain([
          plotted[0]?.at ?? new Date(),
          plotted[plotted.length - 1]?.at ?? new Date(),
        ])
        .range([0, innerW]),
    [plotted, innerW],
  );
  const maxCount = Math.max(...plotted.map((b) => b.count), 1);
  const y = React.useMemo(
    () => scaleLinear().domain([0, maxCount]).nice().range([innerH, 0]),
    [maxCount, innerH],
  );

  const lineGen = React.useMemo(
    () =>
      line<PlottedBucket>()
        .x((d) => x(d.at))
        .y((d) => y(d.count))
        .curve(curveMonotoneX),
    [x, y],
  );
  const areaGen = React.useMemo(
    () =>
      area<PlottedBucket>()
        .x((d) => x(d.at))
        .y0(innerH)
        .y1((d) => y(d.count))
        .curve(curveMonotoneX),
    [x, y, innerH],
  );

  const linePath = lineGen(plotted) ?? "";
  const areaPath = areaGen(plotted) ?? "";

  const totalLength = React.useMemo(() => {
    if (typeof document === "undefined") return 0;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", linePath);
    return path.getTotalLength();
  }, [linePath]);

  function handlePointerMove(e: React.PointerEvent<SVGRectElement>) {
    if (!svgRef.current || plotted.length === 0) return;
    const rect = svgRef.current.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * width - margin.left;
    const target = x.invert(px);
    let closest = 0;
    let closestDist = Infinity;
    plotted.forEach((b, i) => {
      const d = Math.abs(b.at.getTime() - target.getTime());
      if (d < closestDist) {
        closestDist = d;
        closest = i;
      }
    });
    setHoverIndex(closest);
  }

  if (plotted.length === 0 || plotted.every((b) => b.count === 0)) {
    return <p className="text-sm text-moonstone">No commits in this window.</p>;
  }

  const hovered = hoverIndex !== null ? plotted[hoverIndex] : null;
  const last = plotted[plotted.length - 1];

  return (
    <div className={cn("relative", className)}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        role="img"
        aria-label={`Ledger commits over time, ${plotted.length} buckets, peak ${formatCount(maxCount)}`}
        preserveAspectRatio="none"
      >
        <g transform={`translate(${margin.left},${margin.top})`}>
          {/* recessive baseline gridline */}
          <line x1={0} x2={innerW} y1={innerH} y2={innerH} stroke="var(--hairline)" strokeWidth={1} />

          <path d={areaPath} fill="var(--synapse)" opacity={0.1} />

          <motion.path
            d={linePath}
            fill="none"
            stroke="var(--synapse)"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            initial={
              reduced || totalLength === 0
                ? undefined
                : { strokeDasharray: totalLength, strokeDashoffset: totalLength }
            }
            animate={reduced || totalLength === 0 ? undefined : { strokeDashoffset: 0 }}
            transition={{ duration: 1.1, ease: [0.2, 0.7, 0.3, 1] }}
          />

          {/* end marker: >=8px, 2px surface ring */}
          <circle cx={x(last.at)} cy={y(last.count)} r={4} fill="var(--synapse)" stroke="var(--abyss)" strokeWidth={2} />

          {hovered ? (
            <g>
              <line
                x1={x(hovered.at)}
                x2={x(hovered.at)}
                y1={0}
                y2={innerH}
                stroke="var(--hairline)"
                strokeWidth={1}
              />
              <circle
                cx={x(hovered.at)}
                cy={y(hovered.count)}
                r={4}
                fill="var(--synapse)"
                stroke="var(--abyss)"
                strokeWidth={2}
              />
            </g>
          ) : null}

          {/* hit layer covers the whole plot — crosshair finds X, per interaction.md */}
          <rect
            x={0}
            y={0}
            width={innerW}
            height={innerH}
            fill="transparent"
            onPointerMove={handlePointerMove}
            onPointerLeave={() => setHoverIndex(null)}
          />
        </g>
      </svg>

      {hovered ? (
        <div
          className="pointer-events-none absolute top-0 z-10 rounded border border-hairline bg-abyss px-2 py-1 text-xs whitespace-nowrap text-parchment shadow-lg"
          role="tooltip"
          style={{
            left: `min(${(x(hovered.at) / innerW) * 100}%, calc(100% - 8rem))`,
            transform: "translateX(-50%)",
          }}
        >
          <div className="font-semibold tabular text-parchment">
            {formatCount(hovered.count)} commit{hovered.count === 1 ? "" : "s"}
          </div>
          <div className="text-dim">
            {hovered.at.toLocaleString(undefined, {
              month: "short",
              day: "numeric",
              hour: "numeric",
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
