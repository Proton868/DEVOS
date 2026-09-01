/**
 * DevOS DebugInspector — Runtime debugging panel.
 * Shows: breakpoints, stack traces, variable inspector, resource monitoring.
 */
import React, { useState, useEffect } from "react";
import { Bug, Circle, Cpu, MemoryStick, Activity, ChevronRight, ChevronDown, Play, Pause, Square, StepForward } from "lucide-react";
import { api, subscribeToEvents } from "../../services/api";

export default function DebugInspector() {
  const [tab, setTab] = useState("breakpoints");
  const [breakpoints, setBreakpoints] = useState([]);
  const [stackTrace, setStackTrace] = useState([]);
  const [variables, setVariables] = useState({});
  const [resources, setResources] = useState({ cpu: 0, ram: 0, network: 0 });
  const [isRunning, setIsRunning] = useState(false);

  useEffect(() => {
    const unsub = subscribeToEvents(
      (event) => {
        if (event.type === "debug.breakpoint") {
          setBreakpoints((prev) => [...prev, event.data]);
        } else if (event.type === "debug.stacktrace") {
          setStackTrace(event.data.frames || []);
        } else if (event.type === "debug.variables") {
          setVariables(event.data || {});
        } else if (event.type === "debug.resources") {
          setResources(event.data);
        }
      },
      () => {}
    );
    return () => unsub();
  }, []);

  const handleAction = async (action) => {
    try {
      if (action === "continue") { await api.debugContinue(); setIsRunning(true); }
      else if (action === "pause") { await api.debugPause(); setIsRunning(false); }
      else if (action === "stop") { await api.debugStop(); setIsRunning(false); }
      else if (action === "step") { await api.debugStep(); }
    } catch (e) {
      console.error("Debug action failed:", e);
    }
  };

  return (
    <div className="devos-debug-inspector">
      {/* Debug controls */}
      <div className="devos-debug-controls">
        <button
          onClick={() => handleAction(isRunning ? "pause" : "continue")}
          className="devos-debug-btn"
          title={isRunning ? "Pause" : "Continue"}
          aria-label={isRunning ? "Pause execution" : "Continue execution"}
        >
          {isRunning ? <Pause size={12} /> : <Play size={12} />}
        </button>
        <button
          onClick={() => handleAction("step")}
          className="devos-debug-btn"
          title="Step"
          aria-label="Step to next line"
        >
          <StepForward size={12} />
        </button>
        <button
          onClick={() => handleAction("stop")}
          className="devos-debug-btn danger"
          title="Stop"
          aria-label="Stop debugging"
        >
          <Square size={12} />
        </button>
      </div>

      {/* Resource monitor */}
      <div className="devos-debug-resources">
        <ResourceBar icon={Cpu} label="CPU" value={resources.cpu} max={100} unit="%" color="var(--accent)" />
        <ResourceBar icon={MemoryStick} label="RAM" value={resources.ram} max={100} unit="%" color="var(--purple)" />
        <ResourceBar icon={Activity} label="Net" value={resources.network} max={100} unit="%" color="var(--green)" />
      </div>

      {/* Tabs */}
      <div className="devos-debug-tabs">
        <button onClick={() => setTab("breakpoints")} className={tab === "breakpoints" ? "active" : ""}>
          <Bug size={11} /> Breakpoints ({breakpoints.length})
        </button>
        <button onClick={() => setTab("stack")} className={tab === "stack" ? "active" : ""}>
          <Activity size={11} /> Stack
        </button>
        <button onClick={() => setTab("vars")} className={tab === "vars" ? "active" : ""}>
          Variables
        </button>
      </div>

      {/* Tab content */}
      <div className="devos-debug-content">
        {tab === "breakpoints" && (
          <div className="devos-debug-list">
            {breakpoints.length === 0 ? (
              <div className="devos-debug-empty">No breakpoints set</div>
            ) : (
              breakpoints.map((bp, i) => (
                <div key={i} className="devos-breakpoint-item">
                  <Circle size={8} fill="var(--red)" color="var(--red)" />
                  <span className="devos-bp-file">{bp.file}</span>
                  <span className="devos-bp-line">:{bp.line}</span>
                </div>
              ))
            )}
          </div>
        )}

        {tab === "stack" && (
          <div className="devos-debug-list">
            {stackTrace.length === 0 ? (
              <div className="devos-debug-empty">No active stack trace</div>
            ) : (
              stackTrace.map((frame, i) => (
                <div key={i} className="devos-stack-frame">
                  <span className="devos-frame-num">{i}</span>
                  <span className="devos-frame-func">{frame.function || frame.name}</span>
                  <span className="devos-frame-file">{frame.file}:{frame.line}</span>
                </div>
              ))
            )}
          </div>
        )}

        {tab === "vars" && (
          <div className="devos-debug-vars">
            {Object.keys(variables).length === 0 ? (
              <div className="devos-debug-empty">No variables in scope</div>
            ) : (
              <VariableTree variables={variables} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ResourceBar({ icon: Icon, label, value, max, unit, color }) {
  const pct = Math.min(100, (value / max) * 100);
  return (
    <div className="devos-resource-bar">
      <div className="devos-resource-label">
        <Icon size={10} />
        <span>{label}</span>
        <span className="devos-resource-value">{value}{unit}</span>
      </div>
      <div className="devos-resource-track">
        <div className="devos-resource-fill" style={{ width: pct + "%", background: color }} />
      </div>
    </div>
  );
}

function VariableTree({ variables, depth = 0 }) {
  const [expanded, setExpanded] = useState({});
  return (
    <div className="devos-var-tree">
      {Object.entries(variables).map(([key, value]) => {
        const isObject = typeof value === "object" && value !== null;
        const isExpanded = expanded[key];
        return (
          <div key={key} className="devos-var-item" style={{ paddingLeft: depth * 12 }}>
            {isObject ? (
              <button onClick={() => setExpanded({ ...expanded, [key]: !isExpanded })} className="devos-var-toggle">
                {isExpanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
              </button>
            ) : (
              <span className="devos-var-toggle-spacer" />
            )}
            <span className="devos-var-key">{key}</span>
            <span className="devos-var-value">
              {isObject ? "[" + Object.keys(value).length + "]" : JSON.stringify(value)}
            </span>
            {isObject && isExpanded && <VariableTree variables={value} depth={depth + 1} />}
          </div>
        );
      })}
    </div>
  );
}
