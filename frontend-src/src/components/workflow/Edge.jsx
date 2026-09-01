/**
 * DevOS Workflow Edge — Connection between nodes
 * Renders bezier curve edges with animated data flow during execution.
 */
import React, { memo } from "react";

const Edge = memo(function Edge({
  edge,
  sourceNode,
  targetNode,
  isAnimated = false,
}) {
  if (!sourceNode || !targetNode) return null;

  // Calculate port positions
  const sx = sourceNode.x + (sourceNode.width || 200);
  const sy = sourceNode.y + 30; // output port Y
  const tx = targetNode.x;
  const ty = targetNode.y + 30; // input port Y

  // Bezier control points
  const dx = Math.abs(tx - sx);
  const cp1x = sx + dx * 0.5;
  const cp1y = sy;
  const cp2x = tx - dx * 0.5;
  const cp2y = ty;

  const path = `M ${sx} ${sy} C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${tx} ${ty}`;

  return (
    <svg
      className="devos-edge-container"
      style={{
        position: "absolute",
        left: 0,
        top: 0,
        width: "100%",
        height: "100%",
        pointerEvents: "none",
        overflow: "visible",
      }}
    >
      <defs>
        {isAnimated && (
          <linearGradient id={"edge-anim-" + edge.id} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0" />
            <stop offset="50%" stopColor="var(--accent)" stopOpacity="1" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        )}
      </defs>
      {/* Invisible thick path for easier clicking */}
      <path
        d={path}
        fill="none"
        stroke="transparent"
        strokeWidth={12}
        style={{ pointerEvents: "stroke", cursor: "pointer" }}
      />
      {/* Visible edge */}
      <path
        d={path}
        fill="none"
        stroke={isAnimated ? "var(--accent)" : "var(--text-3)"}
        strokeWidth={isAnimated ? 2 : 1.5}
        strokeDasharray={isAnimated ? "6 4" : "none"}
        className={"devos-edge" + (isAnimated ? " animated" : "")}
        style={{
          opacity: isAnimated ? 1 : 0.5,
        }}
      >
        {isAnimated && (
          <animate attributeName="stroke-dashoffset" from="10" to="0" dur="0.6s" repeatCount="indefinite" />
        )}
      </path>
      {/* Arrowhead */}
      <circle cx={tx} cy={ty} r={3} fill={isAnimated ? "var(--accent)" : "var(--text-3)"} />
    </svg>
  );
});

export default Edge;
