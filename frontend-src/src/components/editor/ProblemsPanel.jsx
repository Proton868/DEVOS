import React, { useState } from "react";
import { AlertCircle, AlertTriangle, Info, X, ChevronDown, ChevronRight } from "lucide-react";
import useStore from "../../store/useStore";
import { api, getLanguageFromPath } from "../../services/api";

const SEVERITY = {
  error:   { icon: <AlertCircle size={12} />, color: "#f85149", label: "Error" },
  warning: { icon: <AlertTriangle size={12} />, color: "#d29922", label: "Warning" },
  info:    { icon: <Info size={12} />, color: "#58a6ff", label: "Info" },
};

export default function ProblemsPanel() {
  const { problems, problemsOpen, setProblemsOpen, openFile, setProblems, setStatus } = useStore();
  const [filter, setFilter] = useState("all"); // all | error | warning | info
  const [refreshing, setRefreshing] = useState(false);

  if (!problemsOpen) return null;

  const refresh = async () => {
    setRefreshing(true);
    try {
      // Prefer structured diagnostics endpoint when available
      const res = await api.getDiagnostics?.() || api.getProblems?.();
      const list = res?.problems || res?.diagnostics || res || [];
      setProblems?.(Array.isArray(list) ? list : []);
      setStatus?.(`Diagnostics: ${list.length || 0}`);
    } catch (e) {
      setStatus?.("Diagnostics unavailable");
    } finally {
      setRefreshing(false);
    }
  };

  const byFile = (problems || []).reduce((acc, p) => {
    const path = p.path || p.file || "unknown";
    if (!acc[path]) acc[path] = [];
    acc[path].push({ ...p, path, line: p.line || p.range?.start?.line || 1, col: p.col || p.column || p.range?.start?.character || 1 });
    return acc;
  }, {});

  const filtered = Object.entries(byFile).map(([path, items]) => ({
    path,
    items: items.filter(p => filter === "all" || p.severity === filter),
  })).filter(g => g.items.length > 0);

  const errors   = problems.filter(p => p.severity === "error").length;
  const warnings = problems.filter(p => p.severity === "warning").length;

  const jumpTo = async (problem) => {
    try {
      const { content } = await api.readFile(problem.path);
      openFile({
        path: problem.path,
        name: problem.path.split("/").pop(),
        content,
        language: getLanguageFromPath(problem.path),
      });
      // Reveal line/column in Monaco after the tab activates
      const line = Math.max(1, Number(problem.line) || 1);
      const col = Math.max(1, Number(problem.col) || 1);
      setTimeout(() => {
        window.dispatchEvent(
          new CustomEvent("devos:goto-line", {
            detail: { path: problem.path, line, column: col },
          })
        );
      }, 80);
    } catch {}
  };

  return (
    <div className="problems-panel">
      <div className="problems-header">
        <span>Problems</span>
        <div className="problems-counts">
          <span className="prob-count error"  title="Errors"><AlertCircle size={11} /> {errors}</span>
          <span className="prob-count warning" title="Warnings"><AlertTriangle size={11} /> {warnings}</span>
        </div>
        <div className="problems-filters">
          {["all","error","warning","info"].map(f => (
            <button key={f} className={filter === f ? "active" : ""} onClick={() => setFilter(f)}>
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
        <button onClick={refresh} disabled={refreshing} title="Refresh diagnostics" aria-label="Refresh">
          {refreshing ? "…" : "↻"}
        </button>
        <button onClick={() => setProblemsOpen(false)} aria-label="Close problems"><X size={13} /></button>
      </div>

      <div className="problems-body">
        {problems.length === 0 ? (
          <div className="problems-empty">
            <AlertCircle size={24} color="#3fb950" />
            <p>No problems detected</p>
            <span>LSP diagnostics will appear here when language servers are active.</span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="problems-empty"><p>No {filter}s</p></div>
        ) : (
          filtered.map(({ path, items }) => (
            <FileProblems key={path} path={path} items={items} onJump={jumpTo} />
          ))
        )}
      </div>
    </div>
  );
}

function FileProblems({ path, items, onJump }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="problems-file-group">
      <div className="problems-file-header" onClick={() => setOpen(o => !o)}>
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        <span className="problems-file-name">{path.split("/").pop()}</span>
        <span className="problems-file-path">{path}</span>
        <span className="problems-file-count">{items.length}</span>
      </div>
      {open && items.map((p, i) => {
        const sev = SEVERITY[p.severity] || SEVERITY.info;
        return (
          <div key={i} className="problem-row" onClick={() => onJump(p)}>
            <span className="problem-icon" style={{ color: sev.color }}>{sev.icon}</span>
            <span className="problem-message">{p.message}</span>
            <span className="problem-location">Ln {p.line}, Col {p.col}</span>
            {p.source && <span className="problem-source">{p.source}</span>}
          </div>
        );
      })}
    </div>
  );
}
