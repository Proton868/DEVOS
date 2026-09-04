/**
 * MissionBar — compact OS control strip: branding, project, CMD+K,
 * runtime chips, notifications, user. Mobile collapses secondary chips.
 */
import React, { useEffect, useRef, useState } from "react";
import {
  Search, Bell, ChevronDown, LogOut, Settings, Check, Zap, Terminal, PanelLeft, Menu, X,
} from "lucide-react";
import MenorahLogo from "../MenorahLogo";
import SteelpanSpinner, { useAiBusy } from "../SteelpanSpinner";
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
  const omniOpen = useOsStore((s) => s.omniOpen);
  const setOmniOpen = useOsStore((s) => s.setOmniOpen);
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
  const [openPop, setOpenPop] = useState(null); // projects | notif | user | more
  const [backendUp, setBackendUp] = useState(null);
  const aiBusy = useAiBusy();
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

  const notifCount = (pendingHitlRequests || []).length;

  return (
    <div className="sp-missionbar" ref={barRef}>
      <div className="sp-brand">
        <MenorahLogo size={20} id="mb" />
        <span className="sp-brand-text">DevOS</span>
      </div>

      <div className="sp-mb-proj" style={{ position: "relative" }}>
        <button
          className="sp-project-btn"
          onClick={() => setOpenPop(openPop === "projects" ? null : "projects")}
        >
          <span className="pj-label">{currentProject || "default"}</span>
          <ChevronDown size={12} />
        </button>
        {openPop === "projects" && (
          <div className="sp-mb-pop sp-glass left0">
            {(projects.length ? projects : [currentProject || "default"]).map((p) => (
              <button
                key={p}
                className="sp-mb-item"
                onClick={async () => {
                  setOpenPop(null);
                  if (p !== currentProject) {
                    await setCurrentProject(p);
                    window.dispatchEvent(new CustomEvent("devos:graph-changed"));
                  }
                }}
              >
                {p === currentProject ? <Check size={13} style={{ color: "var(--sp-good)" }} /> : <span style={{ width: 13 }} />}
                {p}
              </button>
            ))}
          </div>
        )}
      </div>

      <button className="sp-cmdk-btn" onClick={() => setCommandBar(true)} title="Command palette (Ctrl/Cmd+K)">
        <Search size={14} />
        <span className="cmdk-label">Search…</span>
        <span className="k">⌘K</span>
      </button>

      <div className="sp-mb-right">
        <button
          className={`sp-chip desktop-only ${!railCollapsed ? "active-chip" : ""}`}
          title={railCollapsed ? "Show sidebar" : "Hide sidebar"}
          onClick={toggleRail}
        >
          <PanelLeft size={13} />
        </button>
        <button
          className={`sp-chip desktop-only ${terminalOpen ? "active-chip" : ""}`}
          title="Toggle terminal"
          onClick={toggleTerminal}
        >
          <Terminal size={13} />
        </button>
        <button
          className={`sp-chip desktop-only ${copilotOpen || chatMode === "docked" ? "active-chip" : ""}`}
          title="AI chat"
          onClick={() => {
            if (copilotOpen) closeCopilot();
            else openCopilot(null, "");
            toggleChatMode();
          }}
        >
          AI
        </button>
        {aiBusy && (
          <span className="sp-chip steelpan-chip" title="AI is working">
            <SteelpanSpinner size={18} />
          </span>
        )}
        <span className={`sp-chip status-chip ${backendUp === false ? "bad" : "ok"}`} title={backendUp ? "Backend online" : "Backend unreachable"}>
          <Zap size={12} />
          <span className="status-pct">{backendUp === false ? "—" : "100%"}</span>
        </span>

        <div style={{ position: "relative" }}>
          <button
            className={`sp-chip ${notifCount ? "has-notif" : ""}`}
            title="Notifications"
            onClick={() => setOpenPop(openPop === "notif" ? null : "notif")}
          >
            <Bell size={13} />
            {notifCount > 0 && <span className="notif-dot">{notifCount}</span>}
          </button>
          {openPop === "notif" && (
            <div className="sp-mb-pop sp-glass right0">
              {notifCount === 0 && (
                <div style={{ padding: "10px 12px", color: "var(--sp-text-2)", fontSize: 12 }}>No pending approvals.</div>
              )}
              {(pendingHitlRequests || []).map((r) => (
                <div key={r.id} className="sp-mb-item" style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
                  <span style={{ fontSize: 12 }}>{r.summary || r.action || r.id}</span>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button className="sp-btn primary" onClick={() => approve(r.id)}>Approve</button>
                    <button className="sp-btn danger" onClick={() => deny(r.id)}>Deny</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{ position: "relative" }} className="desktop-only">
          <button className="sp-chip user-chip" onClick={() => setOpenPop(openPop === "user" ? null : "user")}>
            {userName}
            <ChevronDown size={11} />
          </button>
          {openPop === "user" && (
            <div className="sp-mb-pop sp-glass right0">
              <button className="sp-mb-item" onClick={() => { setOpenPop(null); setOverlay("settings"); }}>
                <Settings size={13} /> Settings
              </button>
              <button className="sp-mb-item" onClick={() => { setOpenPop(null); logout(); }}>
                <LogOut size={13} /> Sign out
              </button>
            </div>
          )}
        </div>

        {/* Mobile overflow menu */}
        <div style={{ position: "relative" }} className="mobile-only">
          <button className="sp-chip" onClick={() => setOpenPop(openPop === "more" ? null : "more")} title="More">
            {openPop === "more" ? <X size={14} /> : <Menu size={14} />}
          </button>
          {openPop === "more" && (
            <div className="sp-mb-pop sp-glass right0">
              <button className="sp-mb-item" onClick={() => { setOpenPop(null); toggleRail(); if (railCollapsed) setOmniOpen(true); }}>
                <PanelLeft size={13} /> {railCollapsed ? "Show" : "Hide"} sidebar
              </button>
              <button className="sp-mb-item" onClick={() => { setOpenPop(null); setOmniOpen(!omniOpen); }}>
                {omniOpen ? "Hide" : "Show"} Omni-Panel
              </button>
              <button className="sp-mb-item" onClick={() => { setOpenPop(null); toggleTerminal(); }}>
                <Terminal size={13} /> {terminalOpen ? "Hide" : "Show"} terminal
              </button>
              <button className="sp-mb-item" onClick={() => { setOpenPop(null); openCopilot(null, ""); }}>
                AI chat
              </button>
              <button className="sp-mb-item" onClick={() => { setOpenPop(null); setOverlay("settings"); }}>
                <Settings size={13} /> Settings
              </button>
              <button className="sp-mb-item" onClick={() => { setOpenPop(null); logout(); }}>
                <LogOut size={13} /> Sign out ({userName})
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
