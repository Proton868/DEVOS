/**
 * MissionBar — compact OS control strip: branding (7-branch menorah),
 * project selector, CMD+K command entry, runtime status, notifications,
 * user controls.
 */
import React, { useEffect, useRef, useState } from "react";
import { Search, Bell, ChevronDown, LogOut, Settings, Check, Zap, Terminal, PanelLeft } from "lucide-react";
import MenorahLogo from "../MenorahLogo";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

export default function MissionBar() {
  const setCommandBar = useOsStore((s) => s.setCommandBar);
  const setOverlay = useOsStore((s) => s.setOverlay);
  const toggleTerminal = useOsStore((s) => s.toggleTerminal);
  const terminalOpen = useOsStore((s) => s.terminal.open);
  const toggleRail = useOsStore((s) => s.toggleRail);
  const railCollapsed = useOsStore((s) => s.railCollapsed);
  const toggleChatMode = useOsStore((s) => s.toggleChatMode);
  const chatMode = useOsStore((s) => s.chatMode);
  const openCopilot = useOsStore((s) => s.openCopilot);
  const closeCopilot = useOsStore((s) => s.closeCopilot);
  const copilotOpen = useOsStore((s) => s.copilot.open);
  const {
    currentProject, setCurrentProject, pendingHitlRequests,
    removePendingHitlRequest, user, logout, setStatus,
  } = useStore();
  const [projects, setProjects] = useState([]);
  const [openPop, setOpenPop] = useState(null); // 'projects' | 'notif' | 'user'
  const [backendUp, setBackendUp] = useState(null);
  const barRef = useRef(null);

  useEffect(() => {
    api.listProjects().then((r) => {
      const raw = Array.isArray(r) ? r : (Array.isArray(r?.projects) ? r.projects : []);
      setProjects(raw.map((p) => (typeof p === "string" ? p : p.name || p.id || p.project_id)).filter(Boolean));
    }).catch(() => setProjects([]));
    api.getIndexStatus()
      .then(() => setBackendUp(true))
      .catch(() => setBackendUp(false));
    const iv = setInterval(() => {
      api.getIndexStatus().then(() => setBackendUp(true)).catch(() => setBackendUp(false));
    }, 30000);
    return () => clearInterval(iv);
  }, []);

  useEffect(() => {
    const close = (e) => {
      if (barRef.current && !barRef.current.contains(e.target)) setOpenPop(null);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, []);

  const userName = user?.username || user?.email || "Operator";

  const approve = async (id) => {
    try { await api.approveHitl(id); removePendingHitlRequest(id); setStatus("HITL approved"); }
    catch (e) { setStatus("Approval failed: " + e.message); }
  };
  const deny = async (id) => {
    try { await api.denyHitl(id); removePendingHitlRequest(id); setStatus("HITL denied"); }
    catch (e) { setStatus("Deny failed: " + e.message); }
  };

  return (
    <div className="sp-missionbar" ref={barRef}>
      <div className="sp-brand">
        <MenorahLogo size={22} id="mb" />
        DevOS
      </div>

      <div style={{ position: "relative" }}>
        <button className="sp-project-btn" onClick={() => setOpenPop(openPop === "projects" ? null : "projects")}>
          <span className="pj-label">{currentProject || "Project"}</span>
          <ChevronDown size={12} />
        </button>
        {openPop === "projects" && (
          <div className="sp-mb-pop sp-glass left0">
            {(projects.length ? projects : [currentProject || "default"]).map((p) => (
              <button
                key={p}
                className="sp-mb-item"
                onClick={async () => { setOpenPop(null); if (p !== currentProject) { await setCurrentProject(p); window.dispatchEvent(new CustomEvent("devos:graph-changed")); } }}
              >
                {p === currentProject ? <Check size={13} style={{ color: "var(--sp-good)" }} /> : <span style={{ width: 13 }} />}
                {p}
              </button>
            ))}
            {projects.length === 0 && (
              <div style={{ padding: "6px 10px", color: "var(--sp-text-2)", fontSize: 11 }}>
                Backend project list unavailable — using current project.
              </div>
            )}
          </div>
        )}
      </div>

      <div className="sp-cmdk-wrap">
        <div className="sp-cmdk-input" onClick={() => setCommandBar(true)} role="button" tabIndex={0}>
          <Search size={13} />
          <span className="cmdk-label">Search projects, agents, workflows, commands…</span>
          <span className="k">CMD+K</span>
        </div>
      </div>

      <div className="sp-mb-right">
        <button
          className={`sp-chip ${!railCollapsed ? "active-chip" : ""}`}
          title={railCollapsed ? "Show Cosmic Sidebar" : "Hide Cosmic Sidebar"}
          onClick={toggleRail}
        >
          <PanelLeft size={13} />
        </button>
        <button
          className={`sp-chip ${terminalOpen ? "active-chip" : ""}`}
          title={terminalOpen ? "Hide Ghost Terminal" : "Show Ghost Terminal (toggle)"}
          onClick={toggleTerminal}
        >
          <Terminal size={13} />
        </button>
        <button
          className={`sp-chip ${copilotOpen ? "active-chip" : ""}`}
          title={copilotOpen ? (chatMode === "floating" ? "Chat floating — click to close" : "Chat docked — Shift+click to float") : "Open AI Copilot Chat"}
          onClick={(e) => {
            if (e.shiftKey && copilotOpen) { toggleChatMode(); return; }
            if (copilotOpen) closeCopilot();
            else openCopilot(null, null);
          }}
        >
          <span style={{ fontSize: 11, fontWeight: 600 }}>AI</span>
        </button>
        <div className="sp-chip" title={backendUp == null ? "Checking backend…" : backendUp ? "Backend online" : "Backend offline"}>
          <Zap size={12} className={backendUp ? "sp-status-ok" : backendUp === false ? "sp-status-off" : ""} />
          <span className={backendUp ? "sp-status-ok" : backendUp === false ? "sp-status-off" : ""}>
            {backendUp == null ? "…" : backendUp ? "100%" : "OFF"}
          </span>
        </div>

        <div style={{ position: "relative" }}>
          <button className="sp-chip" title="Notifications" onClick={() => setOpenPop(openPop === "notif" ? null : "notif")}>
            <Bell size={13} />
            {pendingHitlRequests.length > 0 && <span className="dot-badge" />}
          </button>
          {openPop === "notif" && (
            <div className="sp-mb-pop sp-glass">
              {pendingHitlRequests.length === 0 && (
                <div style={{ padding: "8px 10px", color: "var(--sp-text-2)", fontSize: 12 }}>No pending approvals.</div>
              )}
              {pendingHitlRequests.map((r) => (
                <div key={r.id} style={{ padding: "8px 10px", borderBottom: "1px solid var(--sp-border)" }}>
                  <div style={{ fontSize: 12, color: "var(--sp-text-0)", marginBottom: 6 }}>{r.description || "Human approval required"}</div>
                  <div className="sp-btn-row">
                    <button className="sp-btn" onClick={() => deny(r.id)}>Deny</button>
                    <button className="sp-btn primary" onClick={() => approve(r.id)}><Check size={11} /> Approve</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{ position: "relative" }}>
          <button className="sp-chip" onClick={() => setOpenPop(openPop === "user" ? null : "user")}>
            <span style={{
              width: 18, height: 18, borderRadius: "50%", background: "linear-gradient(135deg, #22d3ee, #a78bfa)",
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              fontSize: 9, fontWeight: 700, color: "#04121a",
            }}>
              {userName.slice(0, 2).toUpperCase()}
            </span>
            {userName}
            <ChevronDown size={11} />
          </button>
          {openPop === "user" && (
            <div className="sp-mb-pop sp-glass">
              <button className="sp-mb-item" onClick={() => { setOpenPop(null); setOverlay("settings"); }}>
                <Settings size={13} /> Settings
              </button>
              <button className="sp-mb-item" onClick={() => logout()}>
                <LogOut size={13} /> Sign out
              </button>
            </div>
          )}
        </div>

        <button className="sp-chip" title="System OS" onClick={() => setOverlay("system")}>
          <Settings size={13} />
        </button>
      </div>
    </div>
  );
}
