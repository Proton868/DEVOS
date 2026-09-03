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
    const st = (active.status || "").toUpperCase();
    if (["RUNNING", "EXECUTING", "IN_PROGRESS"].includes(st)) return "EXECUTING";
    if (["QUEUED", "PENDING"].includes(st)) return "QUEUED";
  }
  return "IDLE";
}

function stateColor(st) {
  switch (st) {
    case "EXECUTING": return "var(--sp-accent)";
    case "THINKING": return "var(--sp-accent)";
    case "QUEUED": return "var(--sp-accent-2)";
    case "IDLE": default: return "rgba(255,255,255,0.12)";
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
              className="sp-agent-card"
              title={w.description || w.role || name}
              onClick={() => openInspector(null) || useOsStore.getState().openCopilot(null, `The user is viewing agent "${name}" (${w.slug || ""}) in the Agency Dashboard.`)}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className="ac-icon"><Icon size={14} /></span>
                <MoreHorizontal size={13} style={{ color: "var(--sp-text-2)" }} />
              </div>
              <div className="ac-name">{name}</div>
              <div className="ac-state" style={{ color: st === "IDLE" ? "var(--sp-text-2)" : stateColor(st) }}>
                {st === "EXECUTING" ? "Executing" : st === "QUEUED" ? "Queued" : "Idle"}
              </div>
              <div className="ac-bar">
                <i style={{
                  width: st === "EXECUTING" ? "72%" : st === "QUEUED" ? "24%" : "0%",
                  background: stateColor(st),
                }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}