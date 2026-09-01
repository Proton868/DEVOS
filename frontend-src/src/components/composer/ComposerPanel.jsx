import React, { useState, useRef } from "react";
import { Zap, X, Check, ChevronDown, ChevronRight, FileText,
         AlertTriangle, Loader, Play, RotateCcw } from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

const RISK_COLORS = { low: "#3fb950", medium: "#d29922", high: "#f85149" };

// ── Diff viewer ──────────────────────────────────────────────
function DiffView({ diff }) {
  const visible = diff.filter(l => l.type !== "ctx" || true).slice(0, 200);
  return (
    <div className="composer-diff">
      {visible.map((l, i) => (
        <div key={i} className={`diff-line ${l.type === "add" ? "diff-add" : l.type === "del" ? "diff-del" : "diff-ctx"}`}>
          <span className="diff-ln">{l.line}</span>
          <span className="diff-sign">{l.type === "add" ? "+" : l.type === "del" ? "-" : " "}</span>
          <pre>{l.text}</pre>
        </div>
      ))}
    </div>
  );
}

// ── File result card ─────────────────────────────────────────
function FileResult({ result, accepted, onToggle }) {
  const [showDiff, setShowDiff] = useState(false);

  if (result.type === "file_skip") return (
    <div className="composer-file-card skipped">
      <FileText size={13} />
      <span className="composer-file-path">{result.path}</span>
      <span className="composer-skip-reason">skipped: {result.reason}</span>
    </div>
  );

  if (result.type !== "file_done") return null;

  return (
    <div className={`composer-file-card ${accepted ? "accepted" : "pending"}`}>
      <div className="composer-file-header">
        <input type="checkbox" checked={accepted} onChange={onToggle} />
        <FileText size={13} />
        <span className="composer-file-path">{result.path}</span>
        <span className="composer-file-changes">
          <span className="diff-add-count">+{result.additions}</span>
          <span className="diff-del-count">-{result.deletions}</span>
        </span>
        <button className="composer-diff-toggle" onClick={() => setShowDiff(s => !s)}>
          {showDiff ? <ChevronDown size={12} /> : <ChevronRight size={12} />} Diff
        </button>
      </div>
      <p className="composer-file-change">{result.change}</p>
      {showDiff && result.diff && <DiffView diff={result.diff} />}
    </div>
  );
}

export default function ComposerPanel() {
  const { composerOpen, setComposerOpen, selectedProvider, selectedModel,
    openTabs, activeTab } = useStore();

  const [instruction, setInstruction] = useState("");
  const [phase, setPhase] = useState("input"); // input | planning | reviewing | executing | done
  const [plan, setPlan] = useState(null);
  const [results, setResults] = useState([]);
  const [accepted, setAccepted] = useState(new Set());
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  const activeFile = openTabs?.find(t => t.path === activeTab);

  const getPlan = async () => {
    setPhase("planning"); setError(null);
    try {
      const p = await api.composerPlan({
        providerId: selectedProvider,
        model: selectedModel,
        instruction,
        activeFile: activeFile ? { path: activeFile.path } : null,
      });
      setPlan(p);
      setPhase("reviewing");
      setAccepted(new Set(p.files.map(f => f.path)));
    } catch (e) {
      setError(e.message);
      setPhase("input");
    }
  };

  const execute = async () => {
    setPhase("executing");
    setResults([]);

    const filteredPlan = { ...plan, files: plan.files.filter(f => accepted.has(f.path)) };

    try {
      const stream = api.streamComposerExecute({
        providerId: selectedProvider,
        model: selectedModel,
        instruction,
        plan: filteredPlan,
        activeFile: activeFile ? { path: activeFile.path } : null,
      });
      for await (const event of stream) {
        if (event.type === "file_done" || event.type === "file_skip") {
          setResults(r => [...r, event]);
          if (event.type === "file_done") {
            setAccepted(a => { const n = new Set(a); n.add(event.path); return n; });
          }
        }
        if (event.type === "compose_done") setPhase("done");
        if (event.type === "error") { setError(event.message); setPhase("done"); }
      }
    } catch (e) {
      setError(e.message);
      setPhase("done");
    }
  };

  const applyChanges = async () => {
    setApplying(true);
    const changes = results
      .filter(r => r.type === "file_done" && accepted.has(r.path))
      .map(r => ({ path: r.path, content: r.updated }));

    try {
      await api.composerApply(changes);
      // Refresh file tree
      if (useStore.getState().setFileTree) {
        api.getTree().then(({ tree }) => useStore.getState().setFileTree(tree || []));
      }
      setComposerOpen(false);
    } catch (e) {
      setError(e.message);
    } finally {
      setApplying(false);
    }
  };

  const reset = () => { setPhase("input"); setPlan(null); setResults([]); setAccepted(new Set()); setError(null); };

  if (!composerOpen) return null;

  return (
    <div className="composer-panel">
      <div className="composer-header">
        <Zap size={15} color="#f59e0b" />
        <span>Composer <span className="composer-badge">Multi-file</span></span>
        <button className="btn-icon" onClick={() => setComposerOpen(false)}><X size={14} /></button>
      </div>

      {phase === "input" && (
        <div className="composer-input-phase">
          <p className="composer-hint">
            Describe a change across your codebase. Composer will plan which files to modify, show you the diffs, and only apply what you approve.
          </p>
          {activeFile && <div className="composer-context-file">📄 Context: {activeFile.path}</div>}
          <textarea
            className="composer-instruction"
            value={instruction}
            onChange={e => setInstruction(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) getPlan(); }}
            placeholder='E.g. "Add input validation and error handling to all form submissions"'
            rows={4}
          />
          <button className="composer-plan-btn" onClick={getPlan} disabled={!instruction.trim()}>
            <Play size={13} /> Plan Changes <kbd>Ctrl+Enter</kbd>
          </button>
        </div>
      )}

      {phase === "planning" && (
        <div className="composer-loading">
          <Loader size={20} className="spin-slow" />
          <p>Analyzing codebase and planning changes…</p>
        </div>
      )}

      {(phase === "reviewing" || phase === "executing" || phase === "done") && plan && (
        <div className="composer-review-phase">
          <div className="composer-plan-summary">
            <p className="composer-summary-text">{plan.summary}</p>
            {plan.notes && <p className="composer-notes"><AlertTriangle size={11} /> {plan.notes}</p>}
          </div>

          {phase === "reviewing" && (
            <>
              <p className="composer-review-label">Select files to include:</p>
              <div className="composer-plan-files">
                {plan.files.map(f => (
                  <div key={f.path} className="composer-plan-file">
                    <input type="checkbox" checked={accepted.has(f.path)}
                      onChange={() => setAccepted(a => { const n = new Set(a); n.has(f.path) ? n.delete(f.path) : n.add(f.path); return n; })} />
                    <span className="composer-plan-path">{f.path}</span>
                    <span className="composer-plan-change">{f.change}</span>
                    <span className="composer-plan-risk" style={{ color: RISK_COLORS[f.risk] }}>{f.risk}</span>
                  </div>
                ))}
              </div>
              <div className="composer-review-actions">
                <button className="btn-secondary" onClick={reset}><RotateCcw size={12} /> Start over</button>
                <button className="composer-execute-btn" onClick={execute} disabled={accepted.size === 0}>
                  <Zap size={13} /> Execute {accepted.size} file{accepted.size !== 1 ? "s" : ""}
                </button>
              </div>
            </>
          )}

          {(phase === "executing" || phase === "done") && (
            <>
              <div className="composer-results">
                {results.map((r, i) => (
                  <FileResult key={i} result={r} accepted={accepted.has(r.path)}
                    onToggle={() => setAccepted(a => { const n = new Set(a); n.has(r.path) ? n.delete(r.path) : n.add(r.path); return n; })} />
                ))}
                {phase === "executing" && <div className="composer-executing"><Loader size={13} className="spin-slow" /> Generating edits…</div>}
              </div>

              {phase === "done" && results.some(r => r.type === "file_done") && (
                <div className="composer-apply-row">
                  <span>{results.filter(r => r.type === "file_done" && accepted.has(r.path)).length} file(s) selected</span>
                  <div>
                    <button className="btn-secondary" onClick={reset}><RotateCcw size={12} /> Redo</button>
                    <button className="composer-apply-btn" onClick={applyChanges} disabled={applying}>
                      {applying ? <Loader size={12} className="spin-slow" /> : <Check size={13} />} Apply Changes
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {error && <div className="composer-error"><AlertTriangle size={12} /> {error}</div>}
    </div>
  );
}
