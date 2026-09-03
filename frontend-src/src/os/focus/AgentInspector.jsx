/**
 * AgentInspector — contextual focus surface for a selected workflow node.
 * Shows REAL data: script config, webhook, chains, and run history.
 * Every action maps to a real API call.
 */
import React, { useEffect, useState } from "react";
import { X, Play, Pencil, Terminal, RefreshCw, Copy, Trash2, Bot } from "lucide-react";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api } from "../../services/api";
import { NodeIcon } from "../canvas/NodeIcon";
import { stateFromRun } from "../canvas/OrchestrationCanvas";

function fmtRun(r) {
  if (!r) return null;
  const status = (r.status || r.state || "unknown").toString().toUpperCase();
  const when = r.started_at || r.created_at || r.timestamp || "";
  return { status, when, id: r.id ?? r.run_id ?? "?" };
}

export default function AgentInspector() {
  const { inspector, closeInspector, nodes, openEditor, openTerminal, openCopilot } = useOsStore();
  const setStatus = useStore((s) => s.setStatus);
  const [runs, setRuns] = useState(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);

  const node = inspector.nodeId ? nodes.find((n) => n.id === inspector.nodeId) : null;
  const sid = node?.scriptId;

  const loadRuns = async () => {
    if (sid == null) return;
    setLoading(true);
    try {
      const r = await api.flowScriptRuns(sid, 10);
      const list = Array.isArray(r) ? r : r?.runs || [];
      setRuns(list);
      const latest = list[0];
      if (latest) useOsStore.getState().setNodeState(sid, stateFromRun(latest), latest);
    } catch (e) {
      setRuns([]);
      setStatus("Failed to load runs: " + e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (inspector.open && sid != null) loadRuns();
  }, [inspector.open, sid]);

  if (!inspector.open) return null;
  if (!node) {
    return (
      <div className="sp-surface" style={{ flex: "0 0 40%", minHeight: 240 }}>
        <div className="sp-surface-head"><span>Agent Inspector</span><span className="spacer" />
          <button className="sp-iconbtn" onClick={closeInspector}><X size={15} /></button></div>
        <div className="sp-insp-body"><span style={{ color: "var(--sp-text-2)" }}>Select a node on the canvas to inspect it.</span></div>
      </div>
    );
  }

  const run = async () => {
    setRunning(true);
    useOsStore.getState().setNodeState(sid, "EXECUTING");
    openTerminal(node.id);
    try {
      await api.runFlowScript(sid);
      setStatus("Execution started");
    } catch (e) {
      setStatus("Run failed: " + e.message);
    } finally {
      setRunning(false);
      loadRuns();
    }
  };

  const removeScript = async () => {
    if (!window.confirm(`Delete workflow "${node.title}"? This cannot be undone.`)) return;
    try {
      await api.deleteFlowScript(sid);
      setStatus("Workflow deleted");
      closeInspector();
      window.dispatchEvent(new CustomEvent("devos:graph-changed"));
    } catch (e) {
      setStatus("Delete failed: " + e.message);
    }
  };

  return (
    <div className="sp-surface" style={{ flex: "0 0 40%", minHeight: 240 }}>
      <div className="sp-surface-head">
        <NodeIcon kind={node.kind} size={14} />
        <span>Agent Inspector</span>
        <span className="sub">· {node.title}</span>
        <span className="spacer" />
        <button className="sp-iconbtn" onClick={closeInspector}><X size={15} /></button>
      </div>
      <div className="sp-insp-body">
        <div className="sp-insp-kv"><span className="k">State</span><span className={`v st-${node.state}`}>{node.state}</span></div>
        <div className="sp-insp-kv"><span className="k">Node ID</span><span className="v">{node.id}</span></div>
        {node.script?.language && <div className="sp-insp-kv"><span className="k">Language</span><span className="v">{node.script.language}</span></div>}
        {node.script?.is_active != null && <div className="sp-insp-kv"><span className="k">Active</span><span className="v">{node.script.is_active ? "yes" : "no"}</span></div>}
        {node.script?.schedule && <div className="sp-insp-kv"><span className="k">Schedule</span><span className="v">{node.script.schedule}</span></div>}

        {node.webhookToken && (
          <>
            <div className="sp-insp-sec">Webhook Trigger</div>
            <div className="sp-logline">{api.webhookUrl(node.webhookToken)}</div>
            <div className="sp-btn-row">
              <button className="sp-btn" onClick={() => { navigator.clipboard?.writeText(api.webhookUrl(node.webhookToken)); setStatus("Webhook URL copied"); }}>
                <Copy size={12} /> Copy URL
              </button>
              <button className="sp-btn" onClick={async () => { try { await api.rotateWebhookToken(sid); setStatus("Webhook token rotated"); } catch (e) { setStatus("Rotate failed: " + e.message); } }}>
                <RefreshCw size={12} /> Rotate token
              </button>
            </div>
          </>
        )}

        <div className="sp-insp-sec">Actions</div>
        <div className="sp-btn-row">
          <button className="sp-btn primary" onClick={run} disabled={running}><Play size={12} /> Execute</button>
          <button className="sp-btn" onClick={() => openEditor({ scriptId: sid })}><Pencil size={12} /> Edit Script</button>
          <button className="sp-btn" onClick={() => openTerminal(node.id)}><Terminal size={12} /> Live Logs</button>
          <button className="sp-btn" onClick={() => openCopilot(node.id, `The user is inspecting the "${node.title}" workflow node.`)}><Bot size={12} /> Ask DevOS</button>
          <button className="sp-btn danger" onClick={removeScript}><Trash2 size={12} /> Delete</button>
        </div>

        <div className="sp-insp-sec">Execution History</div>
        <div className="sp-btn-row" style={{ marginTop: -6 }}>
          <button className="sp-btn" onClick={loadRuns} title="Refresh"><RefreshCw size={11} /> Refresh</button>
        </div>
        {loading && <span style={{ color: "var(--sp-text-2)", fontSize: 12 }}>Loading runs…</span>}
        {!loading && runs && runs.length === 0 && (
          <span style={{ color: "var(--sp-text-2)", fontSize: 12 }}>No runs recorded yet.</span>
        )}
        {runs?.slice(0, 8).map((r, i) => {
          const f = fmtRun(r);
          return (
            <div key={f.id ?? i} className="sp-logline" style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
              <span className={`st-${f.status}`}>{f.status}</span>
              <span style={{ color: "var(--sp-text-2)" }}>{f.when}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
