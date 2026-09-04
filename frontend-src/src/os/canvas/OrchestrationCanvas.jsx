/**
 * OrchestrationCanvas — the DEFAULT and PRIMARY DevOS workspace.
 * A real node-based spatial canvas over DevOS's actual workflow engine:
 *   scripts (/api/scripts)        -> Script / PyRunner Runtime nodes
 *   script webhooks               -> Trigger nodes (real webhook tokens)
 *   script chains (/api/scripts/chains) -> real edges between scripts
 * Node state is derived from REAL run history (/api/scripts/{id}/runs).
 * No fake data, no fabricated execution.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Play, Pencil, RefreshCw, ZoomIn, ZoomOut, Crosshair, Plus } from "lucide-react";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api } from "../../services/api";
import { NodeIcon, slabClass } from "./NodeIcon";

const NODE_W = 168;
const NODE_H = 64;
const COL_GAP = 240;
const ROW_GAP = 150;

/** Map a raw run status to a canonical node state. */
export function stateFromRun(run) {
  if (!run) return "IDLE";
  const s = (run.status || run.state || "").toString().toUpperCase();
  if (["SUCCESS", "SUCCEEDED", "OK", "DONE", "COMPLETED"].includes(s)) return "SUCCESS";
  if (["FAILED", "ERROR", "FAILURE"].includes(s)) return "FAILED";
  if (["RUNNING", "EXECUTING", "STARTED", "IN_PROGRESS"].includes(s)) return "EXECUTING";
  if (["QUEUED", "PENDING"].includes(s)) return "QUEUED";
  return "IDLE";
}

export function buildGraph(scripts, chains) {
  const chainList = Array.isArray(chains)
    ? chains
    : (Array.isArray(chains?.chains) ? chains.chains : (Array.isArray(chains?.items) ? chains.items : []));
  const scriptList = Array.isArray(scripts)
    ? scripts
    : (Array.isArray(scripts?.scripts) ? scripts.scripts : (Array.isArray(scripts?.items) ? scripts.items : []));

  const byId = new Map(scriptList.map((s) => [s.id, s]));
  const parents = new Map();
  for (const ch of chainList) {
    if (byId.has(ch.parent_script_id) && byId.has(ch.child_script_id)) {
      parents.set(ch.child_script_id, [...(parents.get(ch.child_script_id) || []), ch.parent_script_id]);
    }
  }
  const depth = new Map();
  function getDepth(id, seen = new Set()) {
    if (depth.has(id)) return depth.get(id);
    if (seen.has(id)) return 0;
    seen.add(id);
    const ps = parents.get(id) || [];
    const d = ps.length ? Math.max(...ps.map((p) => getDepth(p, seen))) + 1 : 0;
    depth.set(id, d);
    return d;
  }
  scriptList.forEach((s) => getDepth(s.id));

  const colRows = {};
  const pos = new Map();
  for (const s of scriptList) {
    const col = depth.get(s.id) || 0;
    const row = colRows[col] || 0;
    colRows[col] = row + 1;
    pos.set(s.id, { col, row });
  }
  const nodesArr = scriptList.map((s) => {
    const { col, row } = pos.get(s.id);
    return {
      id: `script-${s.id}`,
      scriptId: s.id,
      kind: "runtime",
      title: s.name || `Script ${s.id}`,
      script: s,
      state: "IDLE",
      lastRun: null,
      x: 60 + col * COL_GAP,
      y: 80 + row * ROW_GAP,
      webhookToken: s.webhook_token || null,
    };
  });
  const edges = [];
  for (const n of nodesArr) {
    if (n.webhookToken) {
      nodesArr.push({
        id: `trigger-${n.scriptId}`,
        scriptId: n.scriptId,
        kind: "trigger",
        title: "GitHub Hook",
        state: "IDLE",
        lastRun: null,
        x: n.x - COL_GAP,
        y: n.y,
        webhookToken: n.webhookToken,
      });
      edges.push({ id: `tw-${n.scriptId}`, from: `trigger-${n.scriptId}`, to: n.id, live: false });
    }
  }
  for (const ch of chainList) {
    if (byId.has(ch.parent_script_id) && byId.has(ch.child_script_id)) {
      edges.push({
        id: `chain-${ch.id ?? `${ch.parent_script_id}-${ch.child_script_id}`}`,
        from: `script-${ch.parent_script_id}`,
        to: `script-${ch.child_script_id}`,
        live: false,
      });
    }
  }
  return { nodes: nodesArr, edges };
}

export default function OrchestrationCanvas() {
  const {
    nodes, edges, graphLoading, setGraph, setNodeState, viewport, setViewport,
    selectedNode, selectNode, openEditor, openTerminal, openInspector,
    openCopilot, setCommandBar,
  } = useOsStore();
  const setStatus = useStore((s) => s.setStatus);
  const canvasRef = useRef(null);
  const dragRef = useRef(null);
  const [ctxMenu, setCtxMenu] = useState(null); // { x, y, nodeId }
  const [reloadTick, setReloadTick] = useState(0);

  const loadGraph = useCallback(async () => {
    useOsStore.getState().setGraphLoading(true);
    try {
      const [scripts, chains] = await Promise.all([
        api.flowScripts().catch(() => []),
        api.listChains().catch(() => []),
      ]);
      const { nodes: n, edges: e } = buildGraph(scripts, chains);
      useOsStore.getState().setGraph(n, e);
      await Promise.all(
        n
          .filter((nd) => nd.kind === "runtime" && nd.scriptId != null)
          .slice(0, 40)
          .map(async (nd) => {
            try {
              const runs = await api.flowScriptRuns(nd.scriptId, 1);
              const latest = Array.isArray(runs) ? runs[0] : runs?.runs?.[0];
              useOsStore.getState().setNodeState(nd.scriptId, stateFromRun(latest), latest || null);
            } catch { /* offline — leave IDLE */ }
          })
      );
    } finally {
      useOsStore.getState().setGraphLoading(false);
    }
  }, []);

  useEffect(() => { loadGraph(); }, [loadGraph, reloadTick]);

  // ── Pan / zoom ──────────────────────────────────────────
  const onWheel = useCallback(
    (e) => {
      e.preventDefault();
      const { x, y, zoom } = useOsStore.getState().viewport;
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return;
      if (e.ctrlKey || e.metaKey) {
        const nz = Math.min(2, Math.max(0.3, zoom * (e.deltaY < 0 ? 1.1 : 0.9)));
        const mx = e.clientX - rect.left, my = e.clientY - rect.top;
        setViewport({ x: mx - ((mx - x) * nz) / zoom, y: my - ((my - y) * nz) / zoom, zoom: nz });
      } else {
        setViewport({ x: x - e.deltaX, y: y - e.deltaY, zoom });
      }
    },
    [setViewport]
  );
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [onWheel]);

  const onPointerDown = (e) => {
    if (e.button === 0 && e.target === canvasRef.current) {
      dragRef.current = { sx: e.clientX, sy: e.clientY, ox: viewport.x, oy: viewport.y };
      canvasRef.current.classList.add("panning");
      selectNode(null);
      setCtxMenu(null);
    }
  };
  const onPointerMove = (e) => {
    if (dragRef.current) {
      setViewport({
        ...viewport,
        x: dragRef.current.ox + (e.clientX - dragRef.current.sx),
        y: dragRef.current.oy + (e.clientY - dragRef.current.sy),
      });
    }
  };
  const onPointerUp = () => {
    dragRef.current = null;
    canvasRef.current?.classList.remove("panning");
  };

  const zoomBy = (f) => {
    const { zoom, x, y } = viewport;
    const nz = Math.min(2, Math.max(0.3, zoom * f));
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return setViewport({ ...viewport, zoom: nz });
    const mx = rect.width / 2, my = rect.height / 2;
    setViewport({ x: mx - ((mx - x) * nz) / zoom, y: my - ((my - y) * nz) / zoom, zoom: nz });
  };

  // ── Node actions (all real) ─────────────────────────────
  const runScript = async (scriptId) => {
    setNodeState(scriptId, "EXECUTING");
    setStatus(`Running script #${scriptId}…`);
    openTerminal(`script-${scriptId}`);
    try {
      await api.runFlowScript(scriptId);
      setStatus("Execution started — live output in Ghost Terminal");
    } catch (e) {
      setNodeState(scriptId, "FAILED");
      useStore.getState().setStatus("Run failed: " + e.message);
    }
  };

  const ctxActions = (node) => {
    const sid = node.scriptId;
    if (node.kind === "trigger") {
      return [
        { label: "Inspect Workflow", run: () => openInspector(`script-${sid}`) },
        { label: "Copy Webhook URL", run: () => navigator.clipboard?.writeText(api.webhookUrl(node.webhookToken)) },
        { label: "Run Workflow", run: () => runScript(sid) },
        { label: "Edit Script", run: () => openEditor({ scriptId: sid }) },
      ];
    }
    return [
      { label: "Edit Script (DevOS IDE)", run: () => openEditor({ scriptId: sid }) },
      { label: "Check Logs (PyRunner)", run: () => openTerminal(`script-${sid}`) },
      { label: "Execute", run: () => runScript(sid) },
      { label: "Inspect Agent", run: () => openInspector(`script-${sid}`) },
      { label: "Ask DevOS", run: () => openCopilot(`script-${sid}`, `The user is looking at the "${node.title}" workflow node.`) },
    ];
  };

  const openCtx = (e, nodeId) => {
    e.preventDefault();
    e.stopPropagation();
    selectNode(nodeId);
    setCtxMenu({ x: e.clientX, y: e.clientY, nodeId });
  };

  useEffect(() => {
    if (!ctxMenu) return;
    const close = () => setCtxMenu(null);
    window.addEventListener("click", close);
    window.addEventListener("contextmenu", close);
    return () => { window.removeEventListener("click", close); window.removeEventListener("contextmenu", close); };
  }, [ctxMenu]);

  // ── Render ──────────────────────────────────────────────
  const { x, y, zoom } = viewport;

  const renderEdge = (e) => {
    const a = nodes.find((n) => n.id === e.from);
    const b = nodes.find((n) => n.id === e.to);
    if (!a || !b) return null;
    const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2 - 8;
    const x2 = b.x, y2 = b.y + NODE_H / 2 - 8;
    const mx = (x1 + x2) / 2;
    const d = `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
    const live = [a.state, b.state].some((s) => ["EXECUTING", "RUNNING", "THINKING"].includes(s));
    return (
      <g key={e.id}>
        <path d={d} className={`sp-edge ${live ? "live" : ""}`} />
        <circle cx={x1} cy={y1} r="3" fill="#22d3ee" opacity="0.9" />
        <circle cx={x2} cy={y2} r="3" fill="#a78bfa" opacity="0.9" />
      </g>
    );
  };

  const empty = !graphLoading && nodes.length === 0;
  const ctxNode = ctxMenu && nodes.find((n) => n.id === ctxMenu.nodeId);

  return (
    <div
      ref={canvasRef}
      className="sp-canvas"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerUp}
      onContextMenu={(e) => e.preventDefault()}
    >
      {nodes.length > 0 && (
        <div className="sp-canvas-inner" style={{ transform: `translate(${x}px, ${y}px) scale(${zoom})` }}>
          <svg className="sp-canvas-svg" width={4000} height={3000}>
            <defs>
              <linearGradient id="spEdgeGrad" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0" stopColor="#22d3ee" />
                <stop offset="1" stopColor="#a78bfa" />
              </linearGradient>
            </defs>
            {edges.map(renderEdge)}
          </svg>
          {nodes.map((n) => (
            <div
              key={n.id}
              className={`sp-node st-node-${(n.state || "IDLE").toLowerCase()} ${selectedNode === n.id ? "selected" : ""}`}
              style={{ left: n.x, top: n.y }}
              onClick={(e) => { e.stopPropagation(); selectNode(n.id); }}
              onDoubleClick={(e) => { e.stopPropagation(); openInspector(n.id); }}
              onContextMenu={(e) => openCtx(e, n.id)}
            >
              <div className={`sp-node-state st-${n.state}`}>{n.state}</div>
              <div className="sp-node-chip">
                <div className="sp-node-icon"><NodeIcon kind={n.kind} size={26} /></div>
                <div className={`sp-node-slab ${slabClass(n.kind)}`} />
                <div className="sp-node-quick">
                  {n.scriptId != null && (
                    <button title="Run" onClick={(e) => { e.stopPropagation(); runScript(n.scriptId); }}>
                      <Play size={11} />
                    </button>
                  )}
                  {n.scriptId != null && (
                    <button title="Edit Script" onClick={(e) => { e.stopPropagation(); openEditor({ scriptId: n.scriptId }); }}>
                      <Pencil size={11} />
                    </button>
                  )}
                </div>
              </div>
              <div className="sp-node-label">{n.title}</div>
            </div>
          ))}
        </div>
      )}

      {graphLoading && (
        <div className="sp-canvas-empty">
          <RefreshCw size={22} />
          <span>Loading workflow graph…</span>
        </div>
      )}
      {empty && (
        <div className="sp-canvas-empty">
          <NodeIcon kind="menorah" size={44} />
          <div>
            <div style={{ color: "var(--sp-text-1)", fontWeight: 600 }}>No workflows yet</div>
            <div style={{ fontSize: 12 }}>Use CMD+K → "create workflow" to make your first workflow.</div>
          </div>
          <button className="sp-btn primary" onClick={() => setCommandBar(true, "create workflow ")}>
            <Plus size={13} /> Create Workflow
          </button>
        </div>
      )}

      <div className="sp-canvas-titlebar">
        <span className="sp-canvas-title">Workflow Automation Canvas</span>
        <span className="sp-canvas-brand">
          <img
            src="/static/carai-agency-logo.png"
            alt="CARAI Agency"
            className="sp-carai-logo"
            onError={(e) => {
              e.currentTarget.style.display = "none";
              const s = e.currentTarget.nextElementSibling;
              if (s) s.style.display = "inline";
            }}
          />
          <span style={{ display: "none" }}>CARAI Agency</span>
        </span>
      </div>

      <div className="sp-canvas-hud">
        <button title="Zoom out" onClick={() => zoomBy(0.85)}><ZoomOut size={14} /></button>
        <button title="Zoom in" onClick={() => zoomBy(1.15)}><ZoomIn size={14} /></button>
        <button title="Reset view" onClick={() => setViewport({ x: 80, y: 60, zoom: 1 })}><Crosshair size={14} /></button>
        <button title="Reload graph" onClick={() => setReloadTick((t) => t + 1)}><RefreshCw size={13} /></button>
      </div>

      {ctxMenu && ctxNode && (
        <div className="sp-ctxmenu" style={{ left: ctxMenu.x, top: ctxMenu.y }}>
          <div className="cm-title">{ctxNode.title}</div>
          {ctxActions(ctxNode).map((a) => (
            <button key={a.label} onClick={() => { setCtxMenu(null); a.run(); }}>
              {a.label.startsWith("Execute") || a.label.startsWith("Run") ? <Play size={13} /> :
               a.label.includes("Edit") ? <Pencil size={13} /> : <Crosshair size={13} />}
              {a.label}
            </button>
          ))}
          <button onClick={() => { setCtxMenu(null); setCommandBar(true); }}>
            <ZapIcon /> More actions (CMD+K)
          </button>
        </div>
      )}
    </div>
  );
}

function ZapIcon() {
  return <span style={{ fontSize: 11 }}>⚡</span>;
}
