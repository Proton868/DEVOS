/**
 * OverlaySurface — contextual glass overlay hosting real DevOS capabilities
 * inside the spatial workspace (Files, Git, Search, Memory, MCP, Research,
 * Settings, Execution History, System status). Reuses the existing
 * functional panel components; the canvas remains the primary environment.
 */
import React, { Suspense, lazy, useEffect, useState } from "react";
import { X } from "lucide-react";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api } from "../../services/api";
import { NodeIcon } from "../canvas/NodeIcon";

const FileTree = lazy(() => import("../../components/editor/FileTree"));
const GitPanel = lazy(() => import("../../components/sidebar/GitPanel"));
const SearchPanel = lazy(() => import("../../components/sidebar/SearchPanel"));
const MemoryViewer = lazy(() => import("../../components/agent/MemoryViewer"));
const MCPPanel = lazy(() => import("../../components/mcp/MCPPanel"));
const ResearchPanel = lazy(() => import("../../components/research/ResearchPanel"));
const SettingsModal = lazy(() => import("../../components/settings/SettingsModal"));
const ComposerPanel = lazy(() => import("../../components/composer/ComposerPanel"));

const TITLES = {
  files: "Files",
  git: "Git",
  search: "Search",
  memory: "Memory",
  mcp: "MCP",
  research: "Research",
  settings: "Settings",
  history: "Execution History",
  system: "System OS",
  composer: "Composer",
};

function Fallback() {
  return <div style={{ padding: 16, color: "var(--sp-text-2)", fontSize: 12 }}>Loading…</div>;
}

function ExecutionHistory() {
  const { nodes } = useOsStore();
  const [runs, setRuns] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      const scripts = nodes.filter((n) => n.kind === "runtime" && n.scriptId != null).slice(0, 10);
      const all = await Promise.all(
        scripts.map(async (n) => {
          try {
            const r = await api.flowScriptRuns(n.scriptId, 5);
            const list = Array.isArray(r) ? r : r?.runs || [];
            return list.map((run) => ({ ...run, __node: n.title }));
          } catch { return []; }
        })
      );
      if (alive) {
        setRuns(all.flat().filter(Boolean));
        setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  if (loading) return <Fallback />;
  if (!runs?.length) {
    return (
      <div style={{ padding: 16, color: "var(--sp-text-2)", fontSize: 12 }}>
        No executions recorded yet. Run a workflow node to populate history.
      </div>
    );
  }
  return (
    <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
      {runs.map((r, i) => {
        const status = (r.status || r.state || "unknown").toString().toUpperCase();
        return (
          <div key={r.id ?? i} className="sp-logline" style={{ display: "flex", gap: 12, alignItems: "center" }}>
            <span className={`st-${status}`} style={{ fontWeight: 700 }}>{status}</span>
            <span style={{ color: "var(--sp-text-0)" }}>{r.__node}</span>
            <span style={{ marginLeft: "auto", color: "var(--sp-text-2)" }}>
              {r.started_at || r.created_at || r.timestamp || ""}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function SystemStatus() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    Promise.all([api.ucipHealth().catch((e) => ({ err: e })), api.getIndexStatus().catch(() => null)])
      .then(([g, idx]) => {
        if (g?.err) setErr(g.err.message || "Governance metrics unavailable");
        setData({ governance: g?.err ? null : g, index: idx });
      });
  }, []);
  return (
    <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      {err && <div className="sp-logline lg-error">{err}</div>}
      {data?.governance && (
        <>
          <div className="sp-insp-sec">Governance / UCIP</div>
          <pre style={{
            margin: 0, fontFamily: "var(--sp-mono)", fontSize: 11, color: "var(--sp-text-1)",
            background: "rgba(255,255,255,0.03)", padding: 10, borderRadius: 8, overflowX: "auto",
          }}>{JSON.stringify(data.governance, null, 2)}</pre>
        </>
      )}
      {data?.index && (
        <>
          <div className="sp-insp-sec">Search Index</div>
          <pre style={{
            margin: 0, fontFamily: "var(--sp-mono)", fontSize: 11, color: "var(--sp-text-1)",
            background: "rgba(255,255,255,0.03)", padding: 10, borderRadius: 8, overflowX: "auto",
          }}>{JSON.stringify(data.index, null, 2)}</pre>
        </>
      )}
      {!data && !err && <Fallback />}
    </div>
  );
}

export default function OverlaySurface({ isMobile }) {
  const { overlay, setOverlay } = useOsStore();

  if (!overlay) return null;

  const body = {
    files: <FileTree />,
    git: <GitPanel />,
    search: <SearchPanel />,
    memory: <MemoryViewer />,
    mcp: <MCPPanel onClose={() => setOverlay(null)} />,
    research: <ResearchPanel />,
    settings: <SettingsModal />,
    history: <ExecutionHistory />,
    system: <SystemStatus />,
    composer: <ComposerPanel />,
  }[overlay] || <Fallback />;

  return (
    <div className={`sp-overlay ${isMobile ? "mobile" : ""}`}>
      <div className="sp-surface-head" style={{ borderRadius: 0 }}>
        <NodeIcon kind="menorah" size={14} />
        <span>{TITLES[overlay] || overlay}</span>
        <span className="spacer" />
        <button className="sp-iconbtn" title="Close" onClick={() => setOverlay(null)}><X size={15} /></button>
      </div>
      <div className="sp-overlay-body">
        <Suspense fallback={<Fallback />}>{body}</Suspense>
      </div>
    </div>
  );
}