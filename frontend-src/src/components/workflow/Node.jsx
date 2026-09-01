/**
 * DevOS Workflow Node — Base Component
 * Represents a node in the workflow graph.
 * Node types: Task, Decision, Parallel, Loop, Subworkflow, Trigger
 */
import React, { memo } from "react";
import {
  Play, Pause, Square, GitBranch, Repeat, Box, Zap,
  Cpu, MemoryStick, Clock, User, Activity,
} from "lucide-react";

export const NODE_TYPES = {
  TASK: "task",
  DECISION: "decision",
  PARALLEL: "parallel",
  LOOP: "loop",
  SUBWORKFLOW: "subworkflow",
  TRIGGER: "trigger",
};

export const NODE_STATES = {
  IDLE: "idle",
  THINKING: "thinking",
  EXECUTING: "executing",
  WAITING: "waiting",
  SUCCESS: "success",
  FAILED: "failed",
};

const NODE_ICONS = {
  [NODE_TYPES.TASK]: Play,
  [NODE_TYPES.DECISION]: GitBranch,
  [NODE_TYPES.PARALLEL]: Activity,
  [NODE_TYPES.LOOP]: Repeat,
  [NODE_TYPES.SUBWORKFLOW]: Box,
  [NODE_TYPES.TRIGGER]: Zap,
};

const NODE_COLORS = {
  [NODE_TYPES.TASK]: "var(--accent)",
  [NODE_TYPES.DECISION]: "var(--yellow)",
  [NODE_TYPES.PARALLEL]: "var(--purple)",
  [NODE_TYPES.LOOP]: "var(--cyan, #39c5cf)",
  [NODE_TYPES.SUBWORKFLOW]: "var(--green)",
  [NODE_TYPES.TRIGGER]: "var(--orange, #db6d28)",
};

function Metric({ icon: Icon, label, value }) {
  return (
    <div className="devos-node-metric" title={label + ": " + value}>
      <Icon size={9} />
      <span className="devos-node-metric-value">{value}</span>
    </div>
  );
}

const Node = memo(function Node({
  node,
  isSelected,
  onSelect,
  onMove,
  onStart,
  onStop,
}) {
  const Icon = NODE_ICONS[node.type] || Play;
  const color = NODE_COLORS[node.type] || "var(--accent)";
  const state = node.state || NODE_STATES.IDLE;
  const metrics = node.metrics || {};

  const handleMouseDown = (e) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    onSelect && onSelect(node.id);
    // Drag to move
    const startX = e.clientX;
    const startY = e.clientY;
    const origX = node.x;
    const origY = node.y;
    let moved = false;
    const handleMove = (ev) => {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved = true;
      onMove && onMove(node.id, origX + dx, origY + dy);
    };
    const handleUp = () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
  };

  const isRunning = state === NODE_STATES.EXECUTING;

  return (
    <div
      className={"devos-node devos-node-" + state + (isSelected ? " selected" : "")}
      style={{
        left: node.x,
        top: node.y,
        width: node.width || 200,
        "--node-color": color,
      }}
      onMouseDown={handleMouseDown}
      role="button"
      aria-label={node.title + " node"}
      tabIndex={0}
    >
      {/* Input port */}
      <div className="devos-node-port devos-node-port-in" data-port="in" />

      {/* Node header */}
      <div className="devos-node-header" style={{ borderColor: color }}>
        <Icon size={12} style={{ color }} />
        <span className="devos-node-title">{node.title}</span>
        <div className="devos-node-actions">
          {isRunning ? (
            <button
              onClick={(e) => { e.stopPropagation(); onStop && onStop(node.id); }}
              className="devos-node-btn"
              title="Stop"
              aria-label="Stop execution"
            >
              <Square size={11} />
            </button>
          ) : (
            <button
              onClick={(e) => { e.stopPropagation(); onStart && onStart(node.id); }}
              className="devos-node-btn"
              title="Run"
              aria-label="Run node"
            >
              <Play size={11} />
            </button>
          )}
          {state === NODE_STATES.EXECUTING && (
            <button
              onClick={(e) => { e.stopPropagation(); }}
              className="devos-node-btn"
              title="Pause"
              aria-label="Pause execution"
            >
              <Pause size={11} />
            </button>
          )}
        </div>
      </div>

      {/* Node body — inline metrics */}
      <div className="devos-node-body">
        {node.description && (
          <p className="devos-node-desc">{node.description}</p>
        )}
        {(metrics.cpu || metrics.ram || metrics.queue || metrics.latency) && (
          <div className="devos-node-metrics">
            {metrics.cpu && <Metric icon={Cpu} label="CPU" value={metrics.cpu + "%"} />}
            {metrics.ram && <Metric icon={MemoryStick} label="RAM" value={metrics.ram + "MB"} />}
            {metrics.queue && <Metric icon={Activity} label="Queue" value={metrics.queue} />}
            {metrics.latency && <Metric icon={Clock} label="Latency" value={metrics.latency + "ms"} />}
          </div>
        )}
        {node.owner && (
          <div className="devos-node-owner">
            <User size={9} /> {node.owner}
          </div>
        )}
        {node.lastRun && (
          <div className="devos-node-last-run">Last: {node.lastRun}</div>
        )}
      </div>

      {/* Output port */}
      <div className="devos-node-port devos-node-port-out" data-port="out" />
    </div>
  );
});

export default Node;
