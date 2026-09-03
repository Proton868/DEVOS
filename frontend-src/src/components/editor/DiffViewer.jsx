/**
 * Diff viewer for agent-generated changes (Requirement 4).
 * Uses existing agent_changes infrastructure — accept / reject / revert.
 * Never silently overwrites newer user content; conflict detection is server-side.
 */
import React, { useEffect, useState, useCallback } from "react";
import { X, Check, XCircle, RotateCcw, FileCode, AlertTriangle } from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

function lineDiff(before = "", after = "") {
  const a = (before || "").split("\n");
  const b = (after || "").split("\n");
  const rows = [];
  const max = Math.max(a.length, b.length);
  // Simple side-by-side line comparison (not a full LCS — bounded & readable)
  for (let i = 0; i < max; i++) {
    const left = a[i];
    const right = b[i];
    if (left === right) {
      rows.push({ kind: "same", left, right, n: i + 1 });
    } else if (left == null) {
      rows.push({ kind: "add", left: "", right, n: i + 1 });
    } else if (right == null) {
      rows.push({ kind: "del", left, right: "", n: i + 1 });
    } else {
      rows.push({ kind: "change", left, right, n: i + 1 });
    }
  }
  return rows.slice(0, 2000); // bound output
}

export default function DiffViewer() {
  const {
    diffOpen, setDiffOpen,
    activeAgentTaskId,
    setStatus,
    setFileTree,
  } = useStore();

  const [changes, setChanges] = useState([]);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    const taskId = activeAgentTaskId;
    if (!taskId) {
      setChanges([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await api.listAgentChanges?.(taskId);
      const list = res?.changes || res || [];
      setChanges(Array.isArray(list) ? list : []);
      if (list.length && !selected) setSelected(list[0]);
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [activeAgentTaskId, selected]);

  useEffect(() => {
    if (diffOpen) load();
  }, [diffOpen, load]);

  useEffect(() => {
    if (!selected?.id) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        // Prefer content from list payload; otherwise fetch single change
        if (selected.before_content != null || selected.after_content != null) {
          if (!cancelled) setDetail(selected);
          return;
        }
        const res = await api.getAgentChange?.(selected.id);
        if (!cancelled) setDetail(res?.change || res || selected);
      } catch {
        if (!cancelled) setDetail(selected);
      }
    })();
    return () => { cancelled = true; };
  }, [selected]);

  const accept = async (id) => {
    try {
      await api.acceptAgentChange?.(id);
      setStatus?.(`Accepted change ${id.slice(0, 8)}`);
      await load();
    } catch (e) {
      setStatus?.("Accept failed: " + e.message);
      setError(e.message);
    }
  };

  const reject = async (id) => {
    try {
      await api.rejectAgentChange?.(id);
      setStatus?.(`Rejected change ${id.slice(0, 8)}`);
      setChanges((prev) => prev.filter((c) => c.id !== id));
      setSelected(null);
      setDetail(null);
      api.getTree?.().then(({ tree }) => setFileTree?.(tree || [])).catch(() => {});
    } catch (e) {
      setStatus?.("Reject failed: " + e.message);
      setError(e.message);
    }
  };

  const revert = async (id) => {
    try {
      await api.revertAgentChange?.(id);
      setStatus?.(`Reverted change ${id.slice(0, 8)}`);
      await load();
      api.getTree?.().then(({ tree }) => setFileTree?.(tree || [])).catch(() => {});
    } catch (e) {
      setStatus?.("Revert failed: " + e.message);
      setError(e.message);
    }
  };

  if (!diffOpen) return null;

  const rows = detail
    ? lineDiff(detail.before_content, detail.after_content)
    : [];

  return (
    <div
      className="diff-viewer-panel"
      role="dialog"
      aria-label="Agent change diff"
      style={{
        position: "fixed", inset: "8% 10%", zIndex: 800,
        background: "var(--bg-1, #0f0f14)",
        border: "1px solid var(--border, #333)",
        borderRadius: 8,
        display: "flex", flexDirection: "column",
        boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
        <FileCode size={14} />
        <strong style={{ flex: 1 }}>Agent Changes</strong>
        {!activeAgentTaskId && (
          <span style={{ color: "#f59e0b", fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            <AlertTriangle size={12} /> No active task — open an agent run first
          </span>
        )}
        <button onClick={() => setDiffOpen(false)} aria-label="Close diff"><X size={14} /></button>
      </div>

      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* Change list */}
        <div style={{ width: 260, borderRight: "1px solid var(--border)", overflow: "auto", fontSize: 12 }}>
          {loading && <div style={{ padding: 12, color: "#94a3b8" }}>Loading…</div>}
          {error && <div style={{ padding: 12, color: "#f87171" }}>{error}</div>}
          {!loading && changes.length === 0 && (
            <div style={{ padding: 12, color: "#94a3b8" }}>No recorded agent changes for this task.</div>
          )}
          {changes.map((c) => (
            <button
              key={c.id}
              onClick={() => setSelected(c)}
              style={{
                display: "block", width: "100%", textAlign: "left",
                padding: "8px 10px", border: "none", cursor: "pointer",
                background: selected?.id === c.id ? "var(--bg-3)" : "transparent",
                color: "var(--text, #e2e8f0)", borderBottom: "1px solid var(--border)",
              }}
            >
              <div style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis" }}>{c.path}</div>
              <div style={{ color: "#94a3b8", fontSize: 11 }}>
                {c.change_kind || c.status} · {(c.id || "").slice(0, 8)}
              </div>
            </button>
          ))}
        </div>

        {/* Diff body */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
          {detail && (
            <div style={{ display: "flex", gap: 6, padding: "6px 10px", borderBottom: "1px solid var(--border)" }}>
              <button onClick={() => accept(detail.id)} style={btnStyle}><Check size={12} /> Accept</button>
              <button onClick={() => reject(detail.id)} style={{ ...btnStyle, background: "#7f1d1d" }}><XCircle size={12} /> Reject</button>
              <button onClick={() => revert(detail.id)} style={btnStyle}><RotateCcw size={12} /> Revert</button>
              <span style={{ marginLeft: "auto", fontSize: 11, color: "#94a3b8" }}>{detail.path}</span>
            </div>
          )}
          <div style={{ flex: 1, overflow: "auto", fontFamily: "ui-monospace, monospace", fontSize: 11 }}>
            {rows.map((r, i) => (
              <div
                key={i}
                style={{
                  display: "grid",
                  gridTemplateColumns: "40px 1fr 1fr",
                  background:
                    r.kind === "add" ? "rgba(63,185,80,0.12)"
                    : r.kind === "del" ? "rgba(248,81,73,0.12)"
                    : r.kind === "change" ? "rgba(210,153,34,0.10)"
                    : "transparent",
                }}
              >
                <span style={{ color: "#64748b", padding: "0 6px", textAlign: "right" }}>{r.n}</span>
                <span style={{ padding: "0 6px", whiteSpace: "pre-wrap", borderRight: "1px solid var(--border)" }}>
                  {r.kind === "add" ? "" : r.left}
                </span>
                <span style={{ padding: "0 6px", whiteSpace: "pre-wrap" }}>
                  {r.kind === "del" ? "" : r.right}
                </span>
              </div>
            ))}
            {!detail && !loading && (
              <div style={{ padding: 16, color: "#94a3b8" }}>Select a change to inspect the before/after diff.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

const btnStyle = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  fontSize: 11,
  padding: "4px 8px",
  borderRadius: 4,
  border: "none",
  background: "var(--bg-3)",
  color: "var(--text, #e2e8f0)",
  cursor: "pointer",
};
