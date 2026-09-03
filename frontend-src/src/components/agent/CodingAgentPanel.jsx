/**
 * Coding Agent Panel — Wozzy/Nuha agentic IDE surface.
 * Modes: Ask / Edit / Agent / Review (UX filters; UCIP remains authority).
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Bot, Send, Square, FileCode, Wrench, CheckCircle2,
  XCircle, Loader2, GitBranch, Terminal as TermIcon,
} from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

const MODES = [
  { id: "ask", label: "Ask", hint: "Read-only investigation" },
  { id: "edit", label: "Edit", hint: "Governed file changes" },
  { id: "agent", label: "Agent", hint: "Full dev tools" },
  { id: "review", label: "Review", hint: "Read-only review" },
];

function EventRow({ evt }) {
  const t = evt.type || "";
  const d = evt.data || {};
  if (t === "agent.thinking") {
    return (
      <div className="agent-evt thinking">
        <Loader2 size={12} className="spin" />
        <span>{d.message || "Thinking…"}</span>
      </div>
    );
  }
  if (t === "agent.tool_call") {
    return (
      <div className="agent-evt tool-call">
        <Wrench size={12} />
        <span className="tool-name">{d.tool}</span>
        <span className="tool-meta">
          {d.side_effect ? `side_effect:${d.side_effect}` : ""}
          {d.risk ? ` · risk:${d.risk}` : ""}
        </span>
      </div>
    );
  }
  if (t === "agent.tool_result") {
    const ok = d.ok;
    return (
      <div className={`agent-evt tool-result ${ok ? "ok" : "err"}`}>
        {ok ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
        <span className="tool-name">{d.tool}</span>
        {!ok && d.error ? <span className="tool-err">{String(d.error).slice(0, 200)}</span> : null}
      </div>
    );
  }
  if (t === "agent.file_changed") {
    return (
      <div className="agent-evt file-changed">
        <FileCode size={12} />
        <span>{d.path}</span>
        <span className="tool-meta">
          {d.change}
          {d.additions != null ? ` +${d.additions}` : ""}
          {d.deletions != null ? ` -${d.deletions}` : ""}
        </span>
      </div>
    );
  }
  if (t === "agent.completed") {
    return (
      <div className="agent-evt completed">
        <CheckCircle2 size={12} />
        <span>{d.summary || "Completed"}</span>
      </div>
    );
  }
  if (t === "agent.cancelled") {
    return (
      <div className="agent-evt cancelled">
        <Square size={12} />
        <span>Cancelled</span>
      </div>
    );
  }
  if (t === "agent.error") {
    return (
      <div className="agent-evt err">
        <XCircle size={12} />
        <span>{d.message || "Error"}</span>
      </div>
    );
  }
  if (t === "agent.started") {
    return (
      <div className="agent-evt started">
        <Bot size={12} />
        <span>Agent started · mode {d.mode}</span>
      </div>
    );
  }
  return null;
}

export default function CodingAgentPanel() {
  const {
    selectedProvider, selectedModel, openTabs, activeTab,
    fileTree, setStatus, setFileTree,
  } = useStore();

  const [mode, setMode] = useState("agent");
  const [objective, setObjective] = useState("");
  const [events, setEvents] = useState([]);
  const [running, setRunning] = useState(false);
  const [taskId, setTaskId] = useState(null);
  const [filesChanged, setFilesChanged] = useState([]);
  const abortRef = useRef(null);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events]);

  const stop = useCallback(() => {
    if (abortRef.current) abortRef.current();
    abortRef.current = null;
    if (taskId) {
      api.cancelAgentTask(taskId).catch(() => {});
    }
    setRunning(false);
  }, [taskId]);

  const run = useCallback(() => {
    const text = objective.trim();
    if (!text || running) return;
    setEvents([]);
    setFilesChanged([]);
    setRunning(true);
    setStatus("Agent running…");

    const active = openTabs?.find((t) => t.path === activeTab);
    const context = {
      active_file: active?.path || null,
      selected_text: null,
      open_files: (openTabs || []).map((t) => t.path).slice(0, 20),
      recent_files: [],
      diagnostics: [],
    };

    abortRef.current = api.runAgent({
      objective: text,
      mode,
      provider: selectedProvider,
      model: selectedModel,
      context,
      onEvent: (evt) => {
        if (evt.task_id) setTaskId(evt.task_id);
        if (evt.type === "agent.stream_end") return;
        setEvents((prev) => [...prev.slice(-200), evt]);
        if (evt.type === "agent.file_changed" && evt.data) {
          setFilesChanged((prev) => {
            const next = prev.filter((f) => f.path !== evt.data.path);
            return [...next, evt.data];
          });
        }
        if (evt.type === "agent.completed") {
          setRunning(false);
          setStatus("Agent completed");
          // Refresh file tree after agent edits
          api.getTree?.().then((r) => {
            const tree = r?.files || r?.tree || r;
            if (tree) setFileTree(tree);
          }).catch(() => {});
        }
        if (evt.type === "agent.cancelled" || evt.type === "agent.error") {
          setRunning(false);
          setStatus(evt.type === "agent.error" ? "Agent error" : "Agent cancelled");
        }
      },
      onError: (e) => {
        setRunning(false);
        setStatus("Agent failed: " + e.message);
        setEvents((prev) => [
          ...prev,
          { type: "agent.error", data: { message: e.message } },
        ]);
      },
      onDone: () => setRunning(false),
    });
  }, [
    objective, running, mode, selectedProvider, selectedModel,
    openTabs, activeTab, setStatus, setFileTree,
  ]);

  return (
    <div className="coding-agent-panel flex flex-col h-full" style={{ background: "var(--bg-1)" }}>
      <div className="flex items-center gap-2 px-3 py-2 border-b" style={{ borderColor: "var(--border)" }}>
        <Bot size={14} style={{ color: "var(--accent)" }} />
        <span className="text-xs font-semibold text-slate-200">Wozzy / Nuha · Coding Agent</span>
        <div className="flex-1" />
        <div className="flex gap-1">
          {MODES.map((m) => (
            <button
              key={m.id}
              title={m.hint}
              disabled={running}
              onClick={() => setMode(m.id)}
              className={`px-2 py-0.5 rounded text-[10px] ${mode === m.id ? "font-semibold text-white" : "text-slate-400"}`}
              style={{
                background: mode === m.id ? "var(--accent)" : "var(--bg-3)",
              }}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1 text-xs text-slate-300">
        {events.length === 0 && !running && (
          <div className="text-slate-500 text-xs leading-relaxed py-4">
            Ask the agent to inspect the project, fix tests, apply patches, or review changes.
            All tools pass through UCIP — the agent cannot bypass governance.
          </div>
        )}
        {events.map((evt, i) => (
          <EventRow key={i} evt={evt} />
        ))}
        <div ref={endRef} />
      </div>

      {filesChanged.length > 0 && (
        <div className="px-3 py-2 border-t text-xs" style={{ borderColor: "var(--border)", background: "var(--bg-2)" }}>
          <div className="text-slate-400 mb-1 flex items-center gap-1">
            <GitBranch size={11} /> Files changed
          </div>
          {filesChanged.map((f) => (
            <div key={f.path} className="flex items-center gap-2 text-slate-200">
              <FileCode size={11} />
              <span className="truncate">{f.path}</span>
              <span className="text-slate-500">
                {f.change}
                {f.additions != null ? ` +${f.additions}` : ""}
                {f.deletions != null ? ` -${f.deletions}` : ""}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="p-2 border-t flex gap-2" style={{ borderColor: "var(--border)" }}>
        <textarea
          value={objective}
          onChange={(e) => setObjective(e.target.value)}
          placeholder="e.g. Fix the failing auth tests…"
          rows={2}
          disabled={running}
          className="flex-1 text-xs rounded px-2 py-1.5 resize-none text-slate-100"
          style={{ background: "var(--bg-3)", border: "1px solid var(--border)" }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              run();
            }
          }}
        />
        {running ? (
          <button
            onClick={stop}
            className="px-3 rounded text-xs font-semibold flex items-center gap-1"
            style={{ background: "#7f1d1d", color: "#fff" }}
            title="Stop agent"
          >
            <Square size={12} /> Stop
          </button>
        ) : (
          <button
            onClick={run}
            disabled={!objective.trim()}
            className="px-3 rounded text-xs font-semibold flex items-center gap-1 text-white disabled:opacity-40"
            style={{ background: "var(--accent)" }}
            title="Run agent (Ctrl+Enter)"
          >
            <Send size={12} /> Run
          </button>
        )}
      </div>

      <style>{`
        .agent-evt { display: flex; align-items: flex-start; gap: 6px; padding: 4px 0; }
        .agent-evt .tool-name { font-family: ui-monospace, monospace; color: #93c5fd; }
        .agent-evt .tool-meta { color: #64748b; margin-left: 4px; }
        .agent-evt .tool-err { color: #fca5a5; margin-left: 6px; }
        .agent-evt.err, .agent-evt.tool-result.err { color: #fca5a5; }
        .agent-evt.completed { color: #86efac; }
        .agent-evt.cancelled { color: #fcd34d; }
        .spin { animation: spin 1s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
