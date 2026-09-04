/**
 * Tiny animated steelpan — CARAI cultural busy indicator.
 */
import React from "react";
import useOsStore from "./store/osStore";
import useStore from "../store/useStore";

export default function SteelpanSpinner({ size = 22, title = "AI is working…", className = "" }) {
  const id = React.useId().replace(/:/g, "");
  return (
    <span
      className={`sp-steelpan ${className}`}
      title={title}
      role="status"
      aria-label={title}
      style={{ width: size, height: size, display: "inline-flex", flexShrink: 0 }}
    >
      <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
        <defs>
          <radialGradient id={`spn-rim-${id}`} cx="50%" cy="40%" r="60%">
            <stop offset="0%" stopColor="#e8ecf2" />
            <stop offset="55%" stopColor="#9aa3b2" />
            <stop offset="100%" stopColor="#5c6575" />
          </radialGradient>
          <radialGradient id={`spn-face-${id}`} cx="45%" cy="35%" r="65%">
            <stop offset="0%" stopColor="#f4f6fa" />
            <stop offset="70%" stopColor="#b8c0cc" />
            <stop offset="100%" stopColor="#7a8494" />
          </radialGradient>
        </defs>
        <circle cx="32" cy="32" r="28" fill={`url(#spn-rim-${id})`} stroke="#4a5260" strokeWidth="1.2" />
        <circle cx="32" cy="32" r="24" fill={`url(#spn-face-${id})`} />
        <circle className="spn-ring r1" cx="32" cy="32" r="18" fill="none" stroke="#5a6474" strokeWidth="1.1" opacity="0.75" />
        <circle className="spn-ring r2" cx="32" cy="32" r="12" fill="none" stroke="#5a6474" strokeWidth="1" opacity="0.7" />
        <circle className="spn-ring r3" cx="32" cy="32" r="6" fill="none" stroke="#4a5464" strokeWidth="1.2" opacity="0.85" />
        <circle cx="32" cy="32" r="3.2" fill="#d7dde8" stroke="#6a7384" strokeWidth="0.8" />
        <g className="spn-spark">
          <circle cx="18" cy="20" r="1.4" fill="#fbbf24" />
          <circle cx="46" cy="22" r="1.2" fill="#22d3ee" />
          <circle cx="40" cy="44" r="1.3" fill="#a78bfa" />
        </g>
      </svg>
    </span>
  );
}

export function useAiBusy() {
  const agentTasks = useOsStore((s) => s.agentTasks);
  const nodes = useOsStore((s) => s.nodes);
  const statusMessage = useStore((s) => s.statusMessage);
  const workspaceSettings = useStore((s) => s.workspaceSettings);
  const enabled = workspaceSettings?.ai?.showSteelpanBusy !== false;
  const runningTask = (agentTasks || []).some((t) =>
    ["running", "executing", "thinking", "queued", "in_progress"].includes(
      String(t.status || t.state || "").toLowerCase()
    )
  );
  const runningNode = (nodes || []).some((n) =>
    ["EXECUTING", "RUNNING", "THINKING", "QUEUED"].includes(String(n.state || "").toUpperCase())
  );
  const statusBusy = /running|thinking|generating|executing|working|streaming/i.test(
    String(statusMessage || "")
  );
  return enabled && (runningTask || runningNode || statusBusy);
}
