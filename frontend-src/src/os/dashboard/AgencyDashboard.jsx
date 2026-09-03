/**
 * AgencyDashboard — live agent fleet HUD embedded over the canvas.
 * Real data: /api/workers (agent library) + /api/agent/tasks (live tasks).
 * State is derived from actual task activity — never fabricated.
 */
import React, { useEffect } from "react";
import { X, MoreHorizontal, ChevronDown, Bot, TerminalSquare, Braces, Cpu } from "lucide-react";
import useOsStore from "../store/osStore";
import { api } from "../../services/api";

const ICONS = [Braces, TerminalSquare, Bot, Cpu];

function agentState(w, tasks) {
  const slug = w.slug || w.name;
  const active = tasks.find(
    (t) =>
      (t.worker_slug || t.worker || t.agent || "").toString().toLowerCase() ===
      String(slug).toLowerCase()
  );
  if (active) {
    const st = (active.status || active.state || "").toUpperCase();
    if (["RUNNING", "EXECUTING", "IN_PROGRESS"].includes(st)) return "EXECUTING";
    if (["THINKING", "PLANNING", "REASONING"].includes(st)) return "THINKING";
    if (["QUEUED", "PENDING"].includes(st)) return "QUEUED";
    if (["FAILED", "ERROR"].includes(st)) return "FAILED";
    if (["SUCCESS", "DONE", "COMPLETED"].includes(st)) return "SUCCESS";
  }
  // Fallback: worker-level status if present
  const ws = (w.status || w.state || "").toUpperCase();
  if (["RUNNING", "EXECUTING", "IN_PROGRESS", "BUSY"].includes(ws)) return "EXECUTING";
  if (["THINKING", "PLANNING"].includes(ws)) return "THINKING";
  if (["ONLINE", "READY"].includes(ws)) return "IDLE";
  return "IDLE";
}

function stateColor(st) {
  switch (st) {
    case "EXECUTING": return "var(--sp-accent)";
    case "THINKING": return "var(--sp-accent-2)";
    case "QUEUED": return "var(--sp-warn)";
    case "SUCCESS": return "var(--sp-good)";
    case "FAILED": return "var(--sp-bad)";
    case "IDLE": default: return "rgba(255,255,255,0.12)";
  }
}

function stateLabel(st) {
  switch (st) {
    case "EXECUTING": return "Executing";
    case "THINKING": return "Thinking";
    case "QUEUED": return "Queued";
    case "SUCCESS": return "Success";
    case "FAILED": return "Failed";
    default: return "Idle";
  }
}

function barWidth(st) {
  switch (st) {
    case "EXECUTING": return "78%";
    case "THINKING": return "48%";
    case "QUEUED": return "22%";
    case "SUCCESS": return "100%";
    case "FAILED": return "100%";
    default: return "0%";
  }
}

export default function AgencyDashboard() {
  const { dashboardOpen, setDashboardOpen, workers, setWorkers, agentTasks, setAgentTasks, openInspector } = useOsStore();

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [w, t] = await Promise.all([
          api.getWorkers().catch(() => []),
          api.listAgentTasks().catch(() => []),
        ]);
        if (!alive) return;
        const workerList = Array.isArray(w) ? w : w?.workers || w?.agents || [];
        const taskList = Array.isArray(t) ? t : t?.tasks || [];
        setWorkers(workerList);
        setAgentTasks(taskList);
      } catch { /* offline */ }
    };
    load();
    const iv = setInterval(load, 15000);
    return () => { alive = false; clearInterval(iv); };
  }, []);

  if (!dashboardOpen) return null;

  const shown = workers.slice(0, 6);

  return (
    <div className="sp-agency">
      <div className="sp-agency-head">
        <span className="live" />
        AGENCY DASHBOARD
        <span style={{ marginLeft: "auto", display: "flex", gap: 2 }}>
          <button className="sp-iconbtn" title="Hide dashboard" onClick={() => setDashboardOpen(false)}><X size={14} /></button>
          <button className="sp-iconbtn" title="Collapse" onClick={() => setDashboardOpen(false)}><ChevronDown size={14} /></button>
        </span>
      </div>
      <div className="sp-agency-cards">
        {shown.length === 0 && (
          <span style={{ color: "var(--sp-text-2)", fontSize: 12 }}>
            No agents available from the backend. The agent fleet appears here when /api/workers returns your agent library.
          </span>
        )}
        {shown.map((w, i) => {
          const st = agentState(w, agentTasks);
          const Icon = ICONS[i % ICONS.length];
          const name = w.name || w.slug || `Agent ${i + 1}`;
          return (
            <div
              key={w.slug || w.id || i}
              className={`sp-agent-card ${st.toLowerCase()}`}
              title={w.description || w.role || name}
              onClick={() => {
                useOsStore.getState().openCopilot(null, `The user is viewing agent "${name}" (${w.slug || ""}) in the Agency Dashboard. Status: ${st}.`);
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className="ac-icon"><Icon size={14} /></span>
                <MoreHorizontal size={13} style={{ color: "var(--sp-text-2)" }} />
              </div>
              <div className="ac-name">{name}</div>
              <div className="ac-state" style={{ color: st === "IDLE" ? "var(--sp-text-2)" : stateColor(st) }}>
                {stateLabel(st)}
              </div>
              <div className="ac-bar">
                <i
                  className={st === "EXECUTING" || st === "THINKING" ? "exec-anim" : ""}
                  style={{
                    width: barWidth(st),
                    background: st === "EXECUTING" || st === "THINKING" ? undefined : stateColor(st),
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}