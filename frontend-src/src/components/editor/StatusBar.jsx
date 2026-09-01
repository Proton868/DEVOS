import React, { useEffect, useState } from "react";
import { Terminal, MessageSquare, Settings, GitBranch, Rocket, Database,
         Search, AlertCircle, AlertTriangle, Split, Zap, Play, FileText, Users, ShieldCheck } from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

export default function StatusBar() {
  const {
    statusMessage, terminalOpen, setTerminalOpen,
    chatOpen, setChatOpen, setSettingsOpen,
    agentOpen, setAgentOpen,
    gitOpen, setGitOpen, gitStatus,
    searchOpen, setSearchOpen,
    problemsOpen, setProblemsOpen,
    flowOpen, setFlowOpen,
    workersOpen, setWorkersOpen,
    composerOpen, setComposerOpen,
    setScratchOpen,
    selectedProvider, selectedModel, providers,
    openTabs, activeTab, indexStats, problems,
    splitTab, closeSplit, openFileSplit,
    bgAgents,
  } = useStore();
  const [hitlPending, setHitlPending] = useState([]);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const data = await api.getPendingHitl();
        if (!cancelled) setHitlPending(data.requests || []);
      } catch (err) {
        if (!cancelled) setHitlPending([]);
      }
    };
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, []);

  const activeTab_ = openTabs.find(t => t.path === activeTab);
  const providerInfo = providers[selectedProvider];
  const errors   = problems.filter(p => p.severity === "error").length;
  const warnings = problems.filter(p => p.severity === "warning").length;
  const runningBg = bgAgents.filter(a => a.status === "running").length;

  const closeOthers = () => {
    setChatOpen(false); setAgentOpen(false); setGitOpen(false);
    setSearchOpen(false); setFlowOpen(false); setComposerOpen(false);
    setWorkersOpen(false);
  };

  const toggle = (panel, setter, current) => { closeOthers(); if (!current) setter(true); };

  return (
    <div className="status-bar">
      <div className="status-left">
        <span className="status-item status-branch">
          <GitBranch size={11} />
          {gitStatus?.branch || "main"}
          {gitStatus?.ahead  > 0 && <span className="git-ahead-badge">↑{gitStatus.ahead}</span>}
          {gitStatus?.behind > 0 && <span className="git-behind-badge">↓{gitStatus.behind}</span>}
        </span>
        <span className="status-item">{statusMessage}</span>
        {indexStats?.documents > 0 && (
          <span className="status-item" title={`${indexStats.documents} indexed chunks`}>
            <Database size={10} /> {indexStats.documents}
          </span>
        )}
      </div>

      <div className="status-right">
        {/* Problems */}
        <button
          className={`status-item status-btn ${problemsOpen ? "active" : ""}`}
          onClick={() => setProblemsOpen(!problemsOpen)} title="Problems (Ctrl+Shift+M)"
        >
          {errors   > 0 && <><AlertCircle size={11} color="#f85149" /> {errors}</>}
          {warnings > 0 && <><AlertTriangle size={11} color="#d29922" /> {warnings}</>}
          {errors === 0 && warnings === 0 && <><AlertCircle size={11} /> 0</>}
        </button>

        {/* HITL pending badge */}
        {hitlPending.length > 0 && (
          <span className="status-item status-bg-badge" title={`${hitlPending.length} pending approval(s)`}>
            <ShieldCheck size={11} color="#f59e0b" /> {hitlPending.length}
          </span>
        )}

        {/* Background agents badge */}
        {runningBg > 0 && (
          <span className="status-item status-bg-badge" title={`${runningBg} background agent(s) running`}>
            <Bot size={11} color="#bc8cff" /> {runningBg}
          </span>
        )}

        {/* Active file */}
        {activeTab_ && (
          <>
            <span className="status-item">{activeTab_.language}</span>
            <span className="status-item status-filepath" title={activeTab_.path}>
              {activeTab_.path.split("/").slice(-2).join("/")}
            </span>
            {!splitTab && (
              <button className="status-item status-btn" title="Split editor"
                onClick={() => { const t = openTabs.find(x => x.path === activeTab); if (t) openFileSplit(t); }}>
                <Split size={11} />
              </button>
            )}
            {splitTab && (
              <button className="status-item status-btn active" title="Close split" onClick={closeSplit}>
                <Split size={11} />
              </button>
            )}
          </>
        )}

        {/* Provider */}
        {providerInfo && (
          <button className="status-item status-btn" onClick={() => setSettingsOpen(true)} title="AI Provider (Ctrl+,)">
            {providerInfo.icon} {(selectedModel || providerInfo.defaultModel || "").split("/").pop().slice(0, 16)}
          </button>
        )}

        {/* Panel toggles */}
        <button className={`status-item status-btn ${composerOpen ? "active" : ""}`}
          onClick={() => toggle("composer", setComposerOpen, composerOpen)} title="Composer (Ctrl+Shift+C)">
          <Zap size={11} />
        </button>
        <button className={`status-item status-btn ${flowOpen ? "active" : ""}`}
          onClick={() => toggle("flow", setFlowOpen, flowOpen)} title="Flow (Ctrl+Shift+R)">
          <Play size={11} />
        </button>
        <button className={`status-item status-btn ${gitOpen ? "active" : ""}`}
          onClick={() => toggle("git", setGitOpen, gitOpen)} title="Git (Ctrl+Shift+G)">
          <GitBranch size={11} />
        </button>
        <button className={`status-item status-btn ${searchOpen ? "active" : ""}`}
          onClick={() => toggle("search", setSearchOpen, searchOpen)} title="Search (Ctrl+Shift+F)">
          <Search size={11} />
        </button>
        <button className={`status-item status-btn ${agentOpen ? "active" : ""}`}
          onClick={() => toggle("builder", setAgentOpen, agentOpen)} title="New Project (Builder)">
          <Rocket size={11} />
        </button>
        <button className={`status-item status-btn ${workersOpen ? "active" : ""}`}
          onClick={() => toggle("workers", setWorkersOpen, workersOpen)} title="Workers (Ctrl+Shift+W)">
          <Users size={11} />
        </button>
        <button className={`status-item status-btn ${chatOpen && !agentOpen && !gitOpen && !searchOpen && !flowOpen ? "active" : ""}`}
          onClick={() => toggle("chat", setChatOpen, chatOpen)} title="Chat (Ctrl+Shift+L)">
          <MessageSquare size={11} />
        </button>
        <button className={`status-item status-btn ${terminalOpen ? "active" : ""}`}
          onClick={() => setTerminalOpen(!terminalOpen)} title="Terminal (Ctrl+`)">
          <Terminal size={11} />
        </button>
        <button className="status-item status-btn" onClick={() => setScratchOpen(true)} title="Scratch Pad (Ctrl+Shift+N)">
          <FileText size={11} />
        </button>
        <button className="status-item status-btn" onClick={() => setSettingsOpen(true)} title="Settings (Ctrl+,)">
          <Settings size={11} />
        </button>
      </div>
    </div>
  );
}
