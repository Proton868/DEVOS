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
  const [health, setHealth] = useState(null);
  useEffect(() => {
    Promise.all([
      api.ucipHealth().catch((e) => ({ err: e })),
      api.getIndexStatus().catch(() => null),
      api.getIndexStatus ? fetch("/api/health").then((r) => r.json()).catch(() => null) : null,
    ]).then(([g, idx, h]) => {
      if (g?.err) setErr(g.err.message || "Governance metrics unavailable");
      setData({ governance: g?.err ? null : g, index: idx });
      setHealth(h);
    });
  }, []);

  const g = data?.governance || {};
  const cards = [
    { label: "API Health", value: health?.status || "…", ok: health?.status === "ok" },
    { label: "Database", value: health?.db || "…", ok: health?.db === "ok" },
    { label: "Memory backend", value: health?.memory || "…" },
    { label: "Providers", value: Array.isArray(health?.providers) ? health.providers.join(", ") : (health?.providers || "…") },
    { label: "UCIP status", value: g.status || g.state || "…" },
    { label: "UCIP traces", value: g.total_traces ?? g.traces ?? "—" },
    { label: "Error rate", value: g.recent_error_rate != null ? `${(Number(g.recent_error_rate) * 100).toFixed(1)}%` : "—" },
    { label: "Index", value: data?.index ? (data.index.status || data.index.state || "ready") : "—" },
  ];

  return (
    <div className="sp-system-status">
      <p className="sp-system-lead">
        System OS — live runtime health, governance (UCIP), and search index status.
      </p>
      {err && <div className="sp-logline lg-error">{err}</div>}
      {!data && !err && <Fallback />}
      <div className="sp-system-grid">
        {cards.map((c) => (
          <div key={c.label} className={`sp-system-card ${c.ok === true ? "ok" : c.ok === false ? "bad" : ""}`}>
            <div className="sp-system-label">{c.label}</div>
            <div className="sp-system-value">{String(c.value)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function OverlaySurface({ isMobile }) {
  const { overlay, setOverlay } = useOsStore();

  if (!overlay) return null;

  const close = () => setOverlay(null);
  const body = {
    files: <FileTree />,
    git: <GitPanel embedded onClose={close} />,
    search: <SearchPanel embedded onClose={close} />,
    memory: <MemoryViewer />,
    mcp: <MCPPanel onClose={close} />,
    research: <ResearchPanel embedded onClose={close} />,
    settings: <SettingsModal embedded onClose={close} />,
    history: <ExecutionHistory />,
    system: <SystemStatus />,
    composer: <ComposerPanel embedded onClose={close} />,
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