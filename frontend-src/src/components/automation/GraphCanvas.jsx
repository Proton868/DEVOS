import React, { useMemo } from "react";

const NODES = [
  { id: "github", label: "GitHub Hook", x: 140, y: 110, icon: "GH" },
  { id: "reviewer", label: "Code Reviewer AI", x: 380, y: 70, icon: "AI" },
  { id: "runner", label: "PyRunner Exec", x: 360, y: 260, icon: "Py" },
  { id: "notify", label: "Slack Notify", x: 620, y: 180, icon: "Sl" },
];

const EDGES = [
  { from: "github", to: "reviewer" },
  { from: "reviewer", to: "runner" },
  { from: "runner", to: "notify" },
];

export default function GraphCanvas() {
  const renderedEdges = useMemo(() => {
    return EDGES.map((edge) => {
      const a = NODES.find((n) => n.id === edge.from);
      const b = NODES.find((n) => n.id === edge.to);
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const len = Math.sqrt(dx * dx + dy * dy);
      const nx = dx / len;
      const ny = dy / len;
      const start = { x: a.x + nx * 44, y: a.y + ny * 20 };
      const end = { x: b.x - nx * 44, y: b.y - ny * 20 };
      return { ...edge, x1: start.x, y1: start.y, x2: end.x, y2: end.y };
    });
  }, []);

  return (
    <div className="w-full h-full overflow-hidden relative rounded-xl glass-panel border border-white/[0.08]">
      <div className="absolute inset-0 obsidian-grid opacity-50" />
      <svg className="absolute inset-0 w-full h-full">
        <defs>
          <linearGradient id="edgeGradient" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="rgba(74,222,128,0.1)" />
            <stop offset="100%" stopColor="rgba(74,222,128,0.6)" />
          </linearGradient>
          <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" result="coloredBlur" />
            <feMerge>
              <feMergeNode in="coloredBlur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        {renderedEdges.map((e) => (
          <line
            key={`${e.from}-${e.to}`}
            x1={e.x1}
            y1={e.y1}
            x2={e.x2}
            y2={e.y2}
            stroke="url(#edgeGradient)"
            strokeWidth={2}
            filter="url(#glow)"
          />
        ))}
        {NODES.map((n) => (
          <g key={n.id} transform={`translate(${n.x - 44}, ${n.y - 20})`} className="animate-node-float">
            <rect
              width={88}
              height={40}
              rx={8}
              fill="rgba(16,22,38,0.8)"
              stroke="rgba(74,222,128,0.35)"
              strokeWidth={1}
            />
            <text
              x={44}
              y={25}
              textAnchor="middle"
              fill="#e6edf3"
              fontSize={11}
              fontWeight={600}
            >
              {n.icon} {n.label}
            </text>
          </g>
        ))}
      </svg>
      <div className="absolute bottom-3 left-3 text-[10px] text-slate-500">
        Graph view is visual preview only.
      </div>
    </div>
  );
}
