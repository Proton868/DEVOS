/**
 * AgencyDashboard — live agent fleet HUD embedded over the canvas.
 * Real data: /api/workers (agent library) + /api/agent/tasks (live tasks).
 * State is derived from actual task activity — never fabricated.
 */
import React, { useEffect } from "react";
import { X, MoreHorizontal, ChevronDown, Bot, TerminalSquare, Braces, Cpu } from "lucide-react";
import useOsStore from "../store/osStore";
import { nodeGlowState, planGlowState } from "../orchestrationUi";
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

function AgencyDashboardInner() {
  const { dashboardOpen, setDashboardOpen, workers, setWorkers, agentTasks, setAgentTasks, openInspector, orchestrationMission, openPersonaProfile } = useOsStore();

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
      {orchestrationMission && (
        <div className="sp-mission-strip">
          <div className="sp-mission-head">
            <span className={`sp-mission-badge st-${planGlowState(orchestrationMission.status)}`}>
              {orchestrationMission.status || "mission"}
            </span>
            <span className="sp-mission-goal" title={orchestrationMission.goal}>
              {orchestrationMission.goal || "Nuha mission"}
            </span>
          </div>
          <div className="sp-mission-nodes">
            {(orchestrationMission.nodes || []).map((n) => {
              const st = nodeGlowState(n.status);
              return (
                <button
                  key={n.id}
                  type="button"
                  className={`sp-mission-node st-${st} ${st.toLowerCase()}`}
                  title={`${n.persona_id || ""}: ${n.description || n.id}`}
                  onClick={() => n.persona_id && openPersonaProfile(n.persona_id)}
                >
                  <span className="mn-id">{n.id}</span>
                  <span className="mn-persona">{n.persona_id || "—"}</span>
                  <span className="mn-state">{st}</span>
                  <span className={`mn-glow ${st.toLowerCase()}`} />
                </button>
              );
            })}
          </div>
        </div>
      )}
      <div className="sp-agency-cards">
        {shown.length === 0 && (
          <span style={{ color: "var(--sp-text-2)", fontSize: 12 }}>
            No agents available from the backend. The agent fleet appears here when /api/workers returns your agent library.
          </span>
        )}
        {shown.map((w, i) => {
          let st = agentState(w, agentTasks);
          // Overlay orchestration persona activity onto matching fleet cards
          if (orchestrationMission?.nodes?.length) {
            const slug = (w.slug || w.name || "").toString().toLowerCase();
            const hit = orchestrationMission.nodes.find((n) => {
              const p = (n.persona_id || "").toLowerCase();
              return p && (slug.includes(p) || p.includes(slug.split("-")[0] || ""));
            });
            if (hit) {
              const gst = nodeGlowState(hit.status);
              if (gst !== "IDLE") st = gst;
            }
          }
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

export default function AgencyDashboard() {
  const collapsed = useOsStore((s) => s.layout?.fleetCollapsed !== false);
  const setFleetCollapsed = useOsStore((s) => s.setFleetCollapsed);
  const mission = useOsStore((s) => s.orchestrationMission);
  const activeCount = (mission?.nodes || []).filter(
    (n) => n && ["running", "executing", "active", "in_progress"].includes(String(n.status || "").toLowerCase())
  ).length;

  if (collapsed) {
    return (
      <button
        type="button"
        className="sp-fleet-chip"
        onClick={() => { setFleetCollapsed(false); useOsStore.getState().setDashboardOpen?.(true); }}
        title="Expand Agent Fleet"
      >
        <span className={activeCount ? "sp-fleet-chip-dot active" : "sp-fleet-chip-dot"} />
        <span>Fleet</span>
        {activeCount > 0 ? <span className="sp-fleet-chip-count">{activeCount} active</span> : null}
      </button>
    );
  }

  return (
    <div className="sp-fleet-wrap">
      <div className="sp-fleet-toolbar">
        <span>Agent Fleet</span>
        <button type="button" className="sp-iconbtn" title="Collapse Fleet" onClick={() => setFleetCollapsed(true)}>
          ▾
        </button>
      </div>
      <AgencyDashboardInner />
    </div>
  );
}
