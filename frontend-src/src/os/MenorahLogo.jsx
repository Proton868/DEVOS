import React from "react";

/**
 * DevOS brand mark: a 7-branch menorah (exactly seven branches).
 * Inline SVG so it stays crisp at any size. Gradient accent styling
 * (cyan -> violet) matching the spatial design system.
 */
export default function MenorahLogo({ size = 22, id = "mh" }) {
  const w = size, h = size;
  const c = w / 2;
  // Lamp positions: 7 branches spread across the width, center stem tallest.
  const lamps = [-1, -0.66, -0.33, 0, 0.33, 0.66, 1];
  const topY = h * 0.14;
  const cupY = h * 0.42;
  const baseY = h * 0.88;
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} fill="none" aria-label="DevOS">
      <defs>
        <linearGradient id={`${id}-grad`} x1="0" y1="0" x2={w} y2={h} gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#22d3ee" />
          <stop offset="1" stopColor="#a78bfa" />
        </linearGradient>
      </defs>
      <g stroke={`url(#${id}-grad)`} strokeWidth={Math.max(1.2, w / 26)} strokeLinecap="round" fill="none">
        {lamps.map((t, i) => {
          const x = c + t * (w * 0.4);
          // center branch tallest; outer branches shorter
          const depth = 1 - Math.abs(t) * 0.55;
          const lampY = topY + (1 - depth) * (cupY - topY) * 0.85;
          const curve = t * w * 0.1;
          return (
            <g key={i}>
              {/* branch: from base stem curving up to its lamp */}
              <path d={`M ${c} ${cupY} Q ${c + curve} ${(cupY + lampY) / 2 + 4} ${x} ${lampY}`} />
              {/* lamp cup */}
              <path d={`M ${x - w * 0.045} ${lampY - 2} L ${x + w * 0.045} ${lampY - 2}`} />
            </g>
          );
        })}
        {/* central stem */}
        <path d={`M ${c} ${cupY} L ${c} ${baseY}`} />
        {/* base */}
        <path d={`M ${c - w * 0.2} ${baseY} L ${c + w * 0.2} ${baseY}`} />
        <path d={`M ${c - w * 0.13} ${baseY - h * 0.08} L ${c + w * 0.13} ${baseY - h * 0.08}`} />
      </g>
      {/* flames */}
      {lamps.map((t, i) => {
        const x = c + t * (w * 0.4);
        const depth = 1 - Math.abs(t) * 0.55;
        const lampY = topY + (1 - depth) * (cupY - topY) * 0.85;
        return (
          <circle
            key={`f${i}`}
            cx={x}
            cy={lampY - h * 0.075}
            r={Math.max(1.1, w / 22)}
            fill={`url(#${id}-grad)`}
            opacity="0.95"
          />
        );
      })}
    </svg>
  );
}
