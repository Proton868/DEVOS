/**
 * Agent panel — genuine IDE control surface for HAI/AgentRuntime.
 * Consumes the durable AgentTask event stream (after_seq replay, cancel,
 * modes: ask | edit | agent | review). No second agent event architecture.
 */
import React, { useState, useRef, useEffect, useCallback } from "react";
import {
  Bot, X, Play, Square, RefreshCw, File, Terminal,
  CheckCircle, XCircle, Loader, Search, Edit3, Trash2, FolderPlus,
  AlertTriangle, HelpCircle, RotateCcw,
} from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

const MODES = [
  { id: "ask", label: "Ask", title: "Read-only Q&A" },
  { id: "edit", label: "Edit", title: "Propose edits" },
  { id: "agent", label: "Agent", title: "Autonomous governed loop" },
  { id: "review", label: "Review", title: "Review changes / diffs" },
];

const TOOL_ICONS = {
  read_file: <File size={12} />,
  write_file: <Edit3 size={12} />,
  create_file: <FolderPlus size={12} />,
  delete_file: <Trash2 size={12} />,
  list_files: <File size={12} />,
  search_codebase: <Search size={12} />,
  run_command: <Terminal size={12} />,
  find_in_files: <Search size={12} />,
  apply_patch: <Edit3 size={12} />,
};

const TOOL_COLORS = {
  read_file: "#58a6ff",
  write_file: "#3fb950",
  create_file: "#3fb950",
  delete_file: "#f85149",
  list_files: "#8b949e",
  search_codebase: "#bc8cff",
  run_command: "#f59e0b",
  find_in_files: "#bc8cff",
  apply_patch: "#3fb950",
};

function mapEventToAction(evt) {
  if (!evt || typeof evt !== "object") return null;
  const type = evt.type || evt.event || "";
  const data = evt.data || evt.payload || evt;

  if (type === "agent.started" || type === "start") {
    return { type: "start", taskId: evt.task_id || data.task_id, seq: evt.seq };
  }
  if (type === "agent.thinking" || type === "thinking") {
    return { type: "thinking", text: data.text || data.message || data.thought || "…", seq: evt.seq };
  }
  if (type === "agent.subgoal" || type === "subgoal") {
    return { type: "subgoal", text: data.subgoal || data.text || "", seq: evt.seq };
  }
  if (type === "agent.tool_call" || type === "tool_call") {
    return {
      type: "tool_call",
      tool: data.tool || data.name,
      args: data.args || data.arguments || {},
      seq: evt.seq,
    };
  }
  if (type === "agent.tool_result" || type === "tool_result") {
    return {
      type: "tool_result",
      tool: data.tool || data.name,
      ok: data.ok !== false && !data.error,
      result: data.result || data.output || data,
      seq: evt.seq,
    };
  }
  if (type === "agent.files_changed" || type === "files_changed") {
    return { type: "files_changed", files: data.files || data.paths || [], seq: evt.seq };
  }
  if (type === "agent.verification" || type === "verification") {
    return {
      type: "verification",
      ok: !!data.ok,
      message: data.message || data.summary || "",
      seq: evt.seq,
    };
  }
  if (type === "agent.completed" || type === "done" || type === "completed") {
    return { type: "answer", text: data.message || data.summary || "Completed", seq: evt.seq };
  }
  if (type === "agent.cancelled" || type === "cancelled" || type === "aborted") {
    return { type: "aborted", seq: evt.seq };
  }
  if (type === "agent.blocked" || type === "blocked") {
    return { type: "blocked", message: data.reason || data.message || "Blocked", seq: evt.seq };
  }
  if (type === "agent.unknown" || type === "UNKNOWN") {
    return { type: "unknown", message: data.message || "Result uncertain — investigation required", seq: evt.seq };
  }
  if (type === "agent.error" || type === "error") {
    return { type: "error", message: data.message || data.error || String(data), seq: evt.seq };
  }
  // Pass-through generic
  if (type) return { type: "event", text: type, data, seq: evt.seq };
  return null;
}

function ActionEntry({ action }) {
  const [expanded, setExpanded] = useState(false);

  if (action.type === "start") {
    return (
      <div className="agent-action agent-action-start">
        <Bot size={13} /> Task started
        {action.taskId ? <span className="agent-task-id"> · {action.taskId.slice(0, 8)}</span> : null}
      </div>
    );
  }
  if (action.type === "thinking") {
    return (
      <div className="agent-action agent-action-thinking">
        <Loader size={12} className="spin-slow" />
        <span className="agent-thinking-text">{action.text}…</span>
      </div>
    );
  }
  if (action.type === "subgoal") {
    return (
      <div className="agent-action agent-action-subgoal">
        <Search size={12} />
        <span>Subgoal: {action.text}</span>
      </div>
    );
  }
  if (action.type === "tool_call") {
    const icon = TOOL_ICONS[action.tool] || <Terminal size={12} />;
    const color = TOOL_COLORS[action.tool] || "#8b949e";
    const argSummary =
      action.tool === "run_command"
        ? action.args?.command
        : action.args?.path || action.args?.query || action.args?.pattern || "";
    return (
      <div className="agent-action agent-action-tool" onClick={() => setExpanded((e) => !e)}>
        <span className="agent-tool-icon" style={{ color }}>{icon}</span>
        <span className="agent-tool-name" style={{ color }}>{action.tool}</span>
        <span className="agent-tool-args">{argSummary}</span>
      </div>
    );
  }
  if (action.type === "tool_result") {
    return (
      <div className="agent-action agent-action-result">
        <span className="agent-result-icon">
          {action.ok
            ? <CheckCircle size={11} color="#3fb950" />
            : <XCircle size={11} color="#f85149" />}
        </span>
        <button className="agent-result-toggle" onClick={() => setExpanded((e) => !e)}>
          {action.ok ? "✓" : "✗"} {action.tool} {expanded ? "▲" : "▼"}
        </button>
        {expanded && (
          <pre className="agent-result-json">
            {JSON.stringify(action.result, null, 2).slice(0, 800)}
          </pre>
        )}
      </div>
    );
  }
  if (action.type === "files_changed") {
    return (
      <div className="agent-action agent-action-files">
        <Edit3 size={12} />
        <span>Changed: {(action.files || []).map((f) => (typeof f === "string" ? f : f.path)).join(", ")}</span>
      </div>
    );
  }
  if (action.type === "verification") {
    return (
      <div className="agent-action agent-action-verification">
        {action.ok
          ? <CheckCircle size={12} color="#3fb950" />
          : <XCircle size={12} color="#f85149" />}
        <span>Verification {action.ok ? "passed" : "failed"}{action.message ? `: ${action.message}` : ""}</span>
      </div>
    );
  }
  if (action.type === "answer") {
    return (
      <div className="agent-action agent-action-answer">
        <Bot size={13} color="#3fb950" />
        <div className="agent-answer-text">{action.text}</div>
      </div>
    );
  }
  if (action.type === "aborted") {
    return (
      <div className="agent-action agent-action-aborted">
        <Square size={12} /> Cancelled
      </div>
    );
  }
  if (action.type === "blocked") {
    return (
      <div className="agent-action agent-action-blocked">
        <AlertTriangle size={12} color="#f59e0b" /> {action.message}
      </div>
    );
  }
  if (action.type === "unknown") {
    return (
      <div className="agent-action agent-action-unknown">
        <HelpCircle size={12} color="#a78bfa" /> UNKNOWN — {action.message}
      </div>
    );
  }
  if (action.type === "error") {
    return (
      <div className="agent-action agent-action-error">
        <XCircle size={12} /> {action.message}
      </div>
    );
  }
  if (action.type === "event") {
    return (
      <div className="agent-action agent-action-event">
        <span className="agent-event-type">{action.text}</span>
      </div>
    );
  }
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
    activeTab, openTabs,
    agentMode, setAgentMode,
    activeAgentTaskId, setActiveAgentTaskId,
  } = useStore();

  const [task, setTask] = useState("");
  const [mode, setModeLocal] = useState(agentMode || "agent");
  const [lastSeq, setLastSeq] = useState(0);
  const abortRef = useRef(null);
  const logRef = useRef(null);
  const reconnectTimer = useRef(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [agentActions]);

  useEffect(() => {
    if (agentOpen) {
      api.getIndexStatus?.().then(setIndexStats).catch(() => {});
    }
  }, [agentOpen, setIndexStats]);

  // Sync mode into store for command palette / context
  useEffect(() => {
    setAgentMode?.(mode);
  }, [mode, setAgentMode]);

  const appendFromEvent = useCallback((evt) => {
    if (evt?.seq != null && typeof evt.seq === "number") {
      setLastSeq((s) => Math.max(s, evt.seq));
    }
    if (evt?.task_id) setActiveAgentTaskId?.(evt.task_id);
    const action = mapEventToAction(evt);
    if (action) addAgentAction(action);
  }, [addAgentAction, setActiveAgentTaskId]);

  /** Reconnect after network loss / page refresh using after_seq. */
  const reconnectEvents = useCallback(async (taskId, afterSeq) => {
    if (!taskId) return;
    try {
      const res = await api.getAgentTaskEvents?.(taskId, afterSeq);
      const events = res?.events || [];
      for (const evt of events) appendFromEvent(evt);
      if (res?.status && ["completed", "cancelled", "failed", "blocked"].includes(res.status)) {
        setAgentRunning(false);
      }
    } catch (e) {
      // soft-fail; will retry
    }
  }, [appendFromEvent, setAgentRunning]);

  // On mount / task change, attempt replay if we have a task id
  useEffect(() => {
    if (activeAgentTaskId && agentRunning) {
      reconnectEvents(activeAgentTaskId, lastSeq);
    }
  }, []); // intentional once on mount

  const runAgent = async () => {
    if (!task.trim() || agentRunning) return;
    clearAgentActions();
    setAgentRunning(true);
    setLastSeq(0);
    setActiveAgentTaskId?.(null);
    setStatus("Agent running…");

    // Bounded sanitized context (Requirement 13)
    const tab = (openTabs || []).find((t) => t.path === activeTab);
    const context = {
      current_file: activeTab || null,
      open_files: (openTabs || []).slice(0, 12).map((t) => t.path),
      selected_text: null,
      language: tab?.language || null,
    };

    const stop = api.runAgent({
      objective: task.trim(),
      mode,
      provider: selectedProvider,
      model: selectedModel,
      context,
      onEvent: (evt) => {
        appendFromEvent(evt);
        if (["agent.completed", "agent.cancelled", "agent.error", "done", "completed", "cancelled"].includes(evt?.type)) {
          setAgentRunning(false);
          setStatus("Ready");
        }
      },
      onError: (e) => {
        addAgentAction({ type: "error", message: e?.message || String(e) });
        setAgentRunning(false);
        setStatus("Agent error");
      },
      onDone: () => {
        setAgentRunning(false);
        setStatus("Ready");
        // Refresh tree after possible edits
        api.getTree?.().then(({ tree }) => useStore.getState().setFileTree(tree || [])).catch(() => {});
      },
    });
    abortRef.current = stop;
  };

  const stopAgent = async () => {
    const taskId = activeAgentTaskId || useStore.getState().activeAgentTaskId;
    // Prefer durable cancellation (Stage 3N boundary)
    if (taskId) {
      try {
        await api.cancelAgentTask?.(taskId);
        addAgentAction({ type: "aborted" });
        setStatus("Cancellation requested");
      } catch (e) {
        addAgentAction({ type: "error", message: "Cancel failed: " + (e?.message || e) });
      }
    }
    abortRef.current?.();
    abortRef.current = null;
    setAgentRunning(false);
  };

  const handleReconnect = async () => {
    const taskId = activeAgentTaskId;
    if (!taskId) {
      setStatus("No task to reconnect");
      return;
    }
    setStatus("Reconnecting…");
    await reconnectEvents(taskId, lastSeq);
    setStatus("Reconnected");
  };

  const reindex = async () => {
    setStatus("Re-indexing workspace…");
    try {
      const result = await api.reindex?.();
      setIndexStats(result);
      setStatus(`Indexed ${result?.files ?? "?"} files ✓`);
    } catch (e) {
      setStatus("Reindex failed: " + e.message);
    }
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
    <div className="agent-panel" role="region" aria-label="Agent panel">
      <div className="agent-header">
        <Bot size={15} color="#f59e0b" />
        <span>DevOS Agent</span>
        <div className="agent-header-right">
          {indexStats && (
            <span className="agent-index-badge" title="Indexed documents">
              📚 {indexStats.documents ?? indexStats.files ?? "—"}
            </span>
          )}
          <button title="Reconnect / replay events" onClick={handleReconnect} className="agent-icon-btn" aria-label="Reconnect">
            <RotateCcw size={13} />
          </button>
          <button title="Re-index workspace" onClick={reindex} className="agent-icon-btn" aria-label="Reindex">
            <RefreshCw size={13} />
          </button>
          <button onClick={() => setAgentOpen(false)} className="agent-icon-btn" aria-label="Close agent panel">
            <X size={13} />
          </button>
        </div>
      </div>

      {/* Mode selector (Requirement 12) */}
      <div className="agent-modes" role="tablist" aria-label="Agent mode" style={{ display: "flex", gap: 4, padding: "6px 8px" }}>
        {MODES.map((m) => (
          <button
            key={m.id}
            role="tab"
            aria-selected={mode === m.id}
            title={m.title}
            disabled={agentRunning}
            onClick={() => setModeLocal(m.id)}
            className={mode === m.id ? "agent-mode active" : "agent-mode"}
            style={{
              flex: 1,
              fontSize: 11,
              padding: "4px 6px",
              borderRadius: 4,
              border: mode === m.id ? "1px solid var(--accent)" : "1px solid transparent",
              background: mode === m.id ? "var(--bg-3)" : "transparent",
              color: mode === m.id ? "var(--accent)" : "var(--text-muted, #94a3b8)",
              cursor: agentRunning ? "not-allowed" : "pointer",
            }}
          >
            {m.label}
          </button>
        ))}
      </div>

      {agentActions.length === 0 && (
        <div className="agent-presets">
          <p className="agent-presets-label">Try a task:</p>
          <div className="agent-presets-grid">
            {PRESETS.map((p) => (
              <button key={p} className="agent-preset-chip" onClick={() => setTask(p)}>
                {p}
              </button>
            ))}
          </div>
        </div>
      )}

      {agentActions.length > 0 && (
        <div className="agent-log" ref={logRef} role="log" aria-live="polite">
          {agentActions.map((action, i) => (
            <ActionEntry key={action.seq != null ? `s${action.seq}` : i} action={action} />
          ))}
          {agentRunning && (
            <div className="agent-action agent-action-thinking">
              <Loader size={12} className="spin-slow" /> Working…
            </div>
          )}
        </div>
      )}

      <div className="agent-input-area">
        <textarea
          className="agent-input"
          value={task}
          onChange={(e) => setTask(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !agentRunning) {
              e.preventDefault();
              runAgent();
            }
          }}
          placeholder="Describe a task for the agent… (e.g. 'Add input validation to the login form')"
          rows={3}
          disabled={agentRunning}
          aria-label="Agent objective"
        />
        <div className="agent-input-actions">
          {agentActions.length > 0 && !agentRunning && (
            <button className="btn-secondary agent-clear-btn" onClick={clearAgentActions}>Clear</button>
          )}
          {agentRunning ? (
            <button className="agent-stop-btn" onClick={stopAgent} aria-label="Cancel agent task">
              <Square size={13} /> Cancel
            </button>
          ) : (
            <button className="agent-run-btn" onClick={runAgent} disabled={!task.trim()} aria-label="Run agent">
              <Play size={13} /> Run ({mode})
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
