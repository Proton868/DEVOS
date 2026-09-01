import React, { useState, useRef, useEffect } from "react";
import { Bot, X, Play, Square, RefreshCw, File, Terminal,
         CheckCircle, XCircle, Loader, Search, Edit3, Trash2, FolderPlus } from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

const TOOL_ICONS = {
  read_file:      <File size={12} />,
  write_file:     <Edit3 size={12} />,
  create_file:    <FolderPlus size={12} />,
  delete_file:    <Trash2 size={12} />,
  list_files:     <File size={12} />,
  search_codebase:<Search size={12} />,
  run_command:    <Terminal size={12} />,
  find_in_files:  <Search size={12} />,
};

const TOOL_COLORS = {
  read_file: "#58a6ff", write_file: "#3fb950", create_file: "#3fb950",
  delete_file: "#f85149", list_files: "#8b949e", search_codebase: "#bc8cff",
  run_command: "#f59e0b", find_in_files: "#bc8cff",
};

function ActionEntry({ action }) {
  const [expanded, setExpanded] = useState(false);

  if (action.type === "start") return (
    <div className="agent-action agent-action-start">
      <Bot size={13} /> Task started
    </div>
  );

  if (action.type === "thinking") return (
    <div className="agent-action agent-action-thinking">
      <Loader size={12} className="spin-slow" />
      <span className="agent-thinking-text">{action.text}…</span>
    </div>
  );

  if (action.type === "context") return (
    <div className="agent-action agent-action-context">
      <Search size={12} />
      <span>Context: {action.files.map(f => f.path.split("/").pop()).join(", ")}</span>
    </div>
  );

  if (action.type === "tool_call") {
    const icon = TOOL_ICONS[action.tool] || <Terminal size={12} />;
    const color = TOOL_COLORS[action.tool] || "#8b949e";
    const argSummary = action.tool === "run_command"
      ? action.args.command
      : action.args.path || action.args.query || action.args.pattern || "";
    return (
      <div className="agent-action agent-action-tool" onClick={() => setExpanded(e => !e)}>
        <span className="agent-tool-icon" style={{ color }}>{icon}</span>
        <span className="agent-tool-name" style={{ color }}>{action.tool}</span>
        <span className="agent-tool-args">{argSummary}</span>
      </div>
    );
  }

  if (action.type === "tool_result") {
    const ok = action.ok;
    return (
      <div className="agent-action agent-action-result">
        <span className="agent-result-icon">
          {ok ? <CheckCircle size={11} color="#3fb950" /> : <XCircle size={11} color="#f85149" />}
        </span>
        <button className="agent-result-toggle" onClick={() => setExpanded(e => !e)}>
          {ok ? "✓" : "✗"} {action.tool} {expanded ? "▲" : "▼"}
        </button>
        {expanded && (
          <pre className="agent-result-json">
            {JSON.stringify(action.result, null, 2).slice(0, 800)}
          </pre>
        )}
      </div>
    );
  }

  if (action.type === "answer") return (
    <div className="agent-action agent-action-answer">
      <Bot size={13} color="#3fb950" />
      <div className="agent-answer-text">{action.text}</div>
    </div>
  );

  if (action.type === "aborted") return (
    <div className="agent-action agent-action-aborted">
      <Square size={12} /> Stopped
    </div>
  );

  if (action.type === "error") return (
    <div className="agent-action agent-action-error">
      <XCircle size={12} /> {action.message}
    </div>
  );

  return null;
}

export default function AgentPanel() {
  const {
    agentOpen, setAgentOpen,
    agentActions, addAgentAction, clearAgentActions,
    agentRunning, setAgentRunning,
    selectedProvider, selectedModel,
    indexStats, setIndexStats,
    setStatus,
  } = useStore();

  const [task, setTask] = useState("");
  const abortRef = useRef(null);
  const logRef = useRef(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [agentActions]);

  useEffect(() => {
    if (agentOpen) {
      api.getIndexStatus().then(setIndexStats).catch(() => {});
    }
  }, [agentOpen, setIndexStats]);

  const runAgent = async () => {
    if (!task.trim() || agentRunning) return;
    clearAgentActions();
    setAgentRunning(true);
    setStatus("Agent running…");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const stream = api.streamAgent({
        providerId: selectedProvider,
        model: selectedModel,
        task: task.trim(),
      });

      for await (const action of stream) {
        if (controller.signal.aborted) break;
        addAgentAction(action);
        if (action.type === "done" || action.type === "error") break;
      }
    } catch (e) {
      addAgentAction({ type: "error", message: e.message });
    } finally {
      setAgentRunning(false);
      abortRef.current = null;
      setStatus("Ready");
      // Refresh file tree after agent may have changed files
      const { api: apiMod } = await import("../../services/api");
      apiMod.getTree().then(({ tree }) => useStore.getState().setFileTree(tree || []));
    }
  };

  const stopAgent = () => {
    abortRef.current?.abort();
    setAgentRunning(false);
  };

  const reindex = async () => {
    setStatus("Re-indexing workspace…");
    const result = await api.reindex();
    setIndexStats(result);
    setStatus(`Indexed ${result.files} files ✓`);
    setTimeout(() => setStatus("Ready"), 2000);
  };

  if (!agentOpen) return null;

  const PRESETS = [
    "Add error handling to all async functions",
    "Write unit tests for the main module",
    "Find and fix all TODO comments",
    "Refactor duplicate code into shared utilities",
    "Add TypeScript types to this project",
    "Create a README.md for this project",
  ];

  return (
    <div className="agent-panel">
      <div className="agent-header">
        <Bot size={15} color="#f59e0b" />
        <span>DevOS Agent</span>
        <div className="agent-header-right">
          {indexStats && (
            <span className="agent-index-badge" title="Indexed documents">
              📚 {indexStats.documents}
            </span>
          )}
          <button title="Re-index workspace" onClick={reindex} className="agent-icon-btn">
            <RefreshCw size={13} />
          </button>
          <button onClick={() => setAgentOpen(false)} className="agent-icon-btn">
            <X size={13} />
          </button>
        </div>
      </div>

      {/* Preset tasks */}
      {agentActions.length === 0 && (
        <div className="agent-presets">
          <p className="agent-presets-label">Try a task:</p>
          <div className="agent-presets-grid">
            {PRESETS.map(p => (
              <button key={p} className="agent-preset-chip" onClick={() => setTask(p)}>
                {p}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Action log */}
      {agentActions.length > 0 && (
        <div className="agent-log" ref={logRef}>
          {agentActions.map((action, i) => (
            <ActionEntry key={i} action={action} />
          ))}
          {agentRunning && (
            <div className="agent-action agent-action-thinking">
              <Loader size={12} className="spin-slow" /> Working…
            </div>
          )}
        </div>
      )}

      {/* Task input */}
      <div className="agent-input-area">
        <textarea
          className="agent-input"
          value={task}
          onChange={e => setTask(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !agentRunning) { e.preventDefault(); runAgent(); } }}
          placeholder="Describe a task for the agent… (e.g. 'Add input validation to the login form')"
          rows={3}
          disabled={agentRunning}
        />
        <div className="agent-input-actions">
          {agentActions.length > 0 && !agentRunning && (
            <button className="btn-secondary agent-clear-btn" onClick={clearAgentActions}>Clear</button>
          )}
          {agentRunning ? (
            <button className="agent-stop-btn" onClick={stopAgent}>
              <Square size={13} /> Stop
            </button>
          ) : (
            <button className="agent-run-btn" onClick={runAgent} disabled={!task.trim()}>
              <Play size={13} /> Run Agent
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
