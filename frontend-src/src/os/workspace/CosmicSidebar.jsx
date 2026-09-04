/**
 * CosmicSidebar — persistent left OS navigation.
 * Icon rail + Omni-Panel. Navigation activates contextual views/overlays
 * INSIDE the spatial workspace; the canvas remains the primary environment.
 */
import React, { useEffect, useState } from "react";
import {
  Home, Folder, Bot, Workflow, Cpu, History, GitBranch, Search, Brain,
  Blocks, Settings, ChevronLeft, ChevronRight, Layers, FlaskConical, FilePlus2,
} from "lucide-react";
import MenorahLogo from "../MenorahLogo";
import useOsStore from "../store/osStore";
import { api } from "../../services/api";

const dotColor = (st) =>
  st === "IDLE" ? "rgba(255,255,255,0.18)" :
  ["FAILED", "ERROR"].includes(st) ? "var(--sp-bad)" :
  ["EXECUTING", "RUNNING", "THINKING"].includes(st) ? "var(--sp-good)" : "var(--sp-accent)";

export default function CosmicSidebar() {
  const {
    omniOpen, toggleOmni, overlay, setOverlay, nodes, workers, selectNode,
    openInspector, setDashboardOpen, setCommandBar, railCollapsed, toggleRail,
    openPersonaProfile, openCopilot, setActivePersona, setOmniOpen,
  } = useOsStore();

  const [projects, setProjects] = useState([]);
  const [collapsed, setCollapsed] = useState({ agents: false, workflows: false, projects: false, personas: false });
  const [personaDir, setPersonaDir] = useState([]);
  useEffect(() => {
    api.listPersonas?.().then((r) => setPersonaDir(r.personas || [])).catch(() => {});
  }, []);

  useEffect(() => {
    api.listProjects().then((r) => {
      const raw = Array.isArray(r) ? r : (Array.isArray(r?.projects) ? r.projects : []);
      setProjects(raw.map((p) => (typeof p === "string" ? p : p.name || p.id || p.project_id)).filter(Boolean));
    }).catch(() => setProjects([]));
  }, []);

  const workflowNodes = Array.isArray(nodes) ? nodes.filter((n) => n.kind === "runtime") : [];
  const agentRows = Array.isArray(workers) ? workers.slice(0, 4) : [];

  const railItems = [
    { key: "canvas", icon: Home, title: "Canvas / Omni", onClick: () => { setOverlay(null); useOsStore.getState().setOmniOpen(true); setDashboardOpen(true); } },
    { key: "files", icon: Folder, title: "Files", onClick: () => setOverlay("files") },
    { key: "git", icon: GitBranch, title: "Git", onClick: () => setOverlay("git") },
    { key: "search", icon: Search, title: "Search", onClick: () => setOverlay("search") },
    { key: "history", icon: History, title: "Execution History", onClick: () => setOverlay("history") },
    { key: "composer", icon: FilePlus2, title: "Composer", onClick: () => setOverlay("composer") },
    { key: "research", icon: FlaskConical, title: "Research", onClick: () => setOverlay("research") },
    { key: "memory", icon: Brain, title: "Memory", onClick: () => setOverlay("memory") },
    { key: "mcp", icon: Blocks, title: "MCP", onClick: () => setOverlay("mcp") },
  ];

  if (railCollapsed) {
    return (
      <div className="sp-rail-collapsed-fab">
        <button
          className="sp-rail-expand"
          title="Show Cosmic Sidebar"
          onClick={() => {
            toggleRail();
            // Restore Omni when bringing the rail back so the panel is not "lost"
            useOsStore.getState().setOmniOpen(true);
          }}
        >
          <ChevronRight size={16} />
        </button>
      </div>
    );
  }

  return (
    <div className="sp-cosmic">
      <div className="sp-side-rail">
        <button
          className="sp-rail-btn"
          title="Hide sidebar"
          onClick={toggleRail}
        >
          <ChevronLeft size={16} />
        </button>
        <button
          className={`sp-rail-btn ${omniOpen && !overlay ? "active" : ""}`}
          title={omniOpen ? "Hide Omni-Panel" : "Show Omni-Panel"}
          onClick={() => {
            setOverlay(null);
            toggleOmni();
          }}
        >
          <MenorahLogo size={20} id="rail" />
        </button>
        {railItems.map((r) => (
          <button
            key={r.key}
            className={`sp-rail-btn ${overlay === r.key ? "active" : ""}`}
            title={r.title}
            onClick={r.onClick}
          >
            <r.icon size={17} />
          </button>
        ))}
        <div className="sp-rail-spacer" />
        <button
          className="sp-rail-btn"
          title="Command interface (CMD+K)"
          onClick={() => setCommandBar(true)}
        >
          <Layers size={17} />
        </button>
        <button
          className={`sp-rail-btn ${overlay === "settings" ? "active" : ""}`}
          title="Settings"
          onClick={() => setOverlay("settings")}
        >
          <Settings size={17} />
        </button>
      </div>

      {!omniOpen && (
        <button
          className="sp-omni-pull"
          title="Show Omni-Panel"
          onClick={() => useOsStore.getState().setOmniOpen(true)}
        >
          <ChevronRight size={14} />
          <span>Omni</span>
        </button>
      )}

      {omniOpen && (
        <div className="sp-omni">
          <div className="sp-omni-head">
            Omni-Panel
            <button title="Hide Omni-Panel — reopen with the Omni tab or Menorah icon" onClick={toggleOmni}>
              <ChevronLeft size={15} />
            </button>
          </div>

          <div className="sp-omni-sec">
            <div className="sp-omni-label" onClick={() => setCollapsed((c) => ({ ...c, projects: !c.projects }))}>
              Projects <span>{collapsed.projects ? "›" : "⌄"}</span>
            </div>
            {!collapsed.projects && (projects.length ? projects : []).map((p) => (
              <button
                key={p}
                className="sp-omni-row"
                onClick={() => { setOverlay(null); window.dispatchEvent(new CustomEvent("devos:graph-changed")); }}
              >
                <Folder size={13} /> {p}
                <span className="row-dot" style={{ background: "var(--sp-good)" }} />
              </button>
            ))}
          </div>

          <div className="sp-omni-sec">
            <div className="sp-omni-label" onClick={() => setCollapsed((c) => ({ ...c, personas: !c.personas }))}>
              Personas <span>{collapsed.personas ? "›" : "⌄"}</span>
            </div>
            {!collapsed.personas && (
              <>
                <button
                  className="sp-omni-row"
                  onClick={() => { setActivePersona("nuha"); openCopilot(null, null, "nuha"); }}
                >
                  ✦ Nuha
                  <span className="row-dot" style={{ background: "var(--sp-accent)" }} />
                </button>
                {(personaDir.length ? personaDir : [{ id: "web", display_name: "Web Specialist", level: 1 }]).filter((p) => p.id !== "nuha").slice(0, 8).map((p) => (
                  <button
                    key={p.id}
                    className="sp-omni-row"
                    onClick={() => openPersonaProfile(p.id)}
                    onDoubleClick={() => { setActivePersona(p.id); openCopilot(null, null, p.id); }}
                    title="Click profile · double-click chat"
                  >
                    ◇ {p.display_name || p.name || p.id}
                    <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--sp-text-2)" }}>Lv.{p.level || 1}</span>
                  </button>
                ))}
              </>
            )}
          </div>

          <div className="sp-omni-sec">
            <div className="sp-omni-label" onClick={() => setCollapsed((c) => ({ ...c, agents: !c.agents }))}>
              Agent Fleet <span>{collapsed.agents ? "›" : "⌄"}</span>
            </div>
            {!collapsed.agents && (
              agentRows.length ? agentRows.map((w, i) => (
                <button
                  key={w.slug || w.id || i}
                  className="sp-omni-row"
                  onClick={() => setDashboardOpen(true)}
                  title={w.description || w.slug}
                >
                  <Bot size={13} /> {w.name || w.slug || `Agent ${i + 1}`}
                  <span className="row-dot" style={{ background: dotColor("IDLE") }} />
                </button>
              )) : (
                <div style={{ padding: "4px 8px", color: "var(--sp-text-2)", fontSize: 11.5 }}>Loading agent fleet…</div>
              )
            )}
          </div>

          <div className="sp-omni-sec">
            <div className="sp-omni-label" onClick={() => setCollapsed((c) => ({ ...c, workflows: !c.workflows }))}>
              Workflows <span>{collapsed.workflows ? "›" : "⌄"}</span>
            </div>
            {!collapsed.workflows && (
              workflowNodes.length ? workflowNodes.map((n) => (
                <button
                  key={n.id}
                  className="sp-omni-row"
                  onClick={() => { selectNode(n.id); openInspector(n.id); }}
                >
                  <Workflow size={13} /> {n.title}
                  <span className="row-dot" style={{ background: dotColor(n.state) }} />
                </button>
              )) : (
                <div style={{ padding: "4px 8px", color: "var(--sp-text-2)", fontSize: 11.5 }}>
                  No workflows — CMD+K → "create workflow"
                </div>
              )
            )}
          </div>

          <div className="sp-omni-sec">
            <div className="sp-omni-label">System OS</div>
            <button className="sp-omni-row" onClick={() => setOverlay("system")}>
              <Cpu size={13} /> System
            </button>
            <button className="sp-omni-row" onClick={() => setOverlay("history")}>
              <History size={13} /> Execution History
            </button>
          </div>
        </div>
      )}
    </div>
  );
}