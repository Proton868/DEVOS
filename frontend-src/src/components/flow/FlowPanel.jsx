import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  Play, Trash2, Clock, Key, RefreshCw,
  ChevronRight, ChevronDown, X, Save, Plus,
  CheckCircle, XCircle, Loader, Code, Zap, Download, Package,
  Link2, Copy, Bell, Repeat, GitBranch, List,
} from "lucide-react";
import { api } from "../../services/api";

// Rewritten against the REAL backend (record.md Session 22). The previous
// version had its own separate fetch client pointed at localhost:3001 (the
// Node backend retired in Session 1) with a /api/flow prefix that never
// existed on DevOS, plus field names (packages, schedule, scheduleType,
// s.status) that don't match the real Script model at all. This version
// uses the shared api.js client and the real field names
// (schedule_type/schedule_value, is_active, no packages field), and
// replaces the fake SSE-streaming run view (the backend doesn't stream --
// POST /run returns {status:"queued"} immediately and results only show up
// via GET /runs afterward) with real polling.

// -- Script Editor ------------------------------------------------
const NOTIFY_OPTIONS = [
  { value: "none", label: "Off" },
  { value: "inapp", label: "In-app" },
];
const RETRY_OPTIONS = [
  { value: "none", label: "No retry (1 attempt)" },
  { value: "once", label: "Retry once (2 attempts)" },
  { value: "twice", label: "Retry twice (3 attempts)" },
];

function ScriptEditor({ script, onSave, onClose }) {
  const [name, setName]           = useState(script?.name || "");
  const [code, setCode]           = useState(script?.code || "# Write your Python script here\nprint('Hello from Flow!')");
  const [description, setDesc]    = useState(script?.description || "");
  const [language, setLanguage]   = useState(script?.language || "python");
  const [scheduleType, setSchedT] = useState(script?.schedule_type || "manual");
  const [scheduleValue, setSchedV] = useState(script?.schedule_value || "");
  // Auto-run: whether the schedule is actually enabled (backend field is_active).
  // Defaults to true for existing scripts unless explicitly disabled; new
  // scripts start disabled until a schedule is picked, matching prior UX.
  const [isActive, setIsActive]   = useState(script?.is_active ?? false);
  const [retryPolicy, setRetryPolicy] = useState(script?.retry_policy || "none");
  const [notifySuccess, setNotifySuccess] = useState(script?.notify_on_success || "none");
  const [notifyFailure, setNotifyFailure] = useState(script?.notify_on_failure || "none");
  const [saving, setSaving]       = useState(false);
  const [error, setError]         = useState(null);
  const [copied, setCopied]       = useState(false);
  const [rotating, setRotating]   = useState(false);
  const [webhookToken, setWebhookToken] = useState(script?.webhook_token || null);

  // Dependencies (only usable once the script has been saved / has an id)
  const [pkgInput, setPkgInput]   = useState("");
  const [installing, setInstalling] = useState(false);
  const [installResult, setInstallResult] = useState(null);

  const save = async () => {
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const body = {
        name: name.trim(), description, code, language,
        schedule_type: scheduleType,
        schedule_value: scheduleType === "manual" ? null : scheduleValue,
        is_active: scheduleType === "manual" ? false : isActive,
        retry_policy: retryPolicy,
        notify_on_success: notifySuccess,
        notify_on_failure: notifyFailure,
      };
      if (script?.id) await api.updateFlowScript(script.id, body);
      else await api.createFlowScript(body);
      onSave();
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  };

  const copyWebhookUrl = () => {
    if (!webhookToken) return;
    navigator.clipboard?.writeText(api.webhookUrl(webhookToken));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const rotateToken = async () => {
    if (!script?.id) return;
    setRotating(true);
    try {
      const { webhook_token } = await api.rotateWebhookToken(script.id);
      setWebhookToken(webhook_token);
    } catch (e) { setError(e.message); }
    finally { setRotating(false); }
  };

  const installDeps = async () => {
    if (!script?.id || !pkgInput.trim()) return;
    setInstalling(true);
    setInstallResult(null);
    try {
      const packages = pkgInput.split(/[\s,]+/).map(p => p.trim()).filter(Boolean);
      const result = await api.installPackages(script.id, packages);
      setInstallResult({ ok: true, message: `Installed: ${packages.join(", ")}` });
      setPkgInput("");
    } catch (e) {
      setInstallResult({ ok: false, message: e.message || "Install failed" });
    } finally {
      setInstalling(false);
    }
  };

  return (
    <div className="flow-editor">
      <div className="flow-editor-header">
        <input className="flow-editor-name" value={name} onChange={e => setName(e.target.value)} placeholder="Script name" />
        <div className="flow-editor-actions">
          <button className="btn-primary" onClick={save} disabled={saving || !name.trim()}>
            {saving ? <Loader size={13} className="spin-slow" /> : <Save size={13} />} Save
          </button>
          <button className="btn-icon" onClick={onClose}><X size={14} /></button>
        </div>
      </div>

      {error && <div className="flow-error" role="alert">{error}</div>}

      <div className="flow-editor-body">
        <textarea className="flow-code-area" value={code} onChange={e => setCode(e.target.value)} spellCheck={false} />

        <div className="flow-editor-meta">
          <label className="flow-meta-row">
            <span>Description</span>
            <input value={description} onChange={e => setDesc(e.target.value)} placeholder="Optional description" />
          </label>
          <label className="flow-meta-row">
            <span>Language</span>
            <select value={language} onChange={e => setLanguage(e.target.value)}>
              <option value="python">Python</option>
              <option value="node">Node.js</option>
              <option value="bash">Bash</option>
            </select>
          </label>
          <label className="flow-meta-row">
            <span><Clock size={12} /> Schedule</span>
            <select value={scheduleType} onChange={e => setSchedT(e.target.value)}>
              <option value="manual">Manual only</option>
              <option value="interval">Interval (seconds)</option>
              <option value="cron">Cron</option>
            </select>
          </label>
          {scheduleType === "interval" && (
            <label className="flow-meta-row">
              <span>Every N seconds</span>
              <input value={scheduleValue} onChange={e => setSchedV(e.target.value)} placeholder="300" />
            </label>
          )}
          {scheduleType === "cron" && (
            <label className="flow-meta-row">
              <span>Cron expr</span>
              <input value={scheduleValue} onChange={e => setSchedV(e.target.value)} placeholder="0 9 * * 1-5" />
            </label>
          )}
          {scheduleType !== "manual" && (
            <label className="flow-meta-row">
              <span><Zap size={12} /> Auto-run</span>
              <div className={`toggle ${isActive ? "on" : ""}`} onClick={() => setIsActive(v => !v)}>
                <div className="toggle-thumb" />
              </div>
              <span style={{ fontSize: 11, color: "var(--text-3)", marginLeft: 8 }}>
                {isActive ? "Scheduler will run this automatically" : "Schedule saved but paused"}
              </span>
            </label>
          )}

          <label className="flow-meta-row">
            <span><Repeat size={12} /> Retry on failure</span>
            <select value={retryPolicy} onChange={e => setRetryPolicy(e.target.value)}>
              {RETRY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className="flow-meta-row">
            <span><Bell size={12} /> Notify on success</span>
            <select value={notifySuccess} onChange={e => setNotifySuccess(e.target.value)}>
              {NOTIFY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className="flow-meta-row">
            <span><Bell size={12} /> Notify on failure</span>
            <select value={notifyFailure} onChange={e => setNotifyFailure(e.target.value)}>
              {NOTIFY_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>

          {script?.id && (
            <div className="flow-meta-row" style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
              <span><Link2 size={12} /> Webhook URL</span>
              <div style={{ display: "flex", gap: 6 }}>
                <input readOnly style={{ flex: 1 }} value={webhookToken ? api.webhookUrl(webhookToken) : "(save script to generate)"} onClick={e => e.target.select()} />
                <button className="btn-secondary-sm" onClick={copyWebhookUrl} disabled={!webhookToken} title="Copy webhook URL">
                  <Copy size={12} /> {copied ? "Copied!" : "Copy"}
                </button>
                <button className="btn-secondary-sm" onClick={rotateToken} disabled={rotating || !script?.id} title="Rotate webhook token">
                  {rotating ? <Loader size={12} className="spin-slow" /> : <RefreshCw size={12} />} Rotate
                </button>
              </div>
              <span style={{ fontSize: 11, color: "var(--text-3)" }}>
                POST to this URL (no auth) to trigger this script remotely. Rotate if leaked.
              </span>
            </div>
          )}

          {script?.id && (
            <div className="flow-meta-row" style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
              <span><Package size={12} /> Dependencies ({language === "node" ? "npm" : language === "python" ? "pip" : "unsupported"})</span>
              {language === "bash" ? (
                <span style={{ fontSize: 11, color: "var(--text-3)" }}>Dependency install isn't supported for Bash scripts.</span>
              ) : (
                <div style={{ display: "flex", gap: 6 }}>
                  <input
                    style={{ flex: 1 }}
                    placeholder={language === "node" ? "e.g. axios lodash" : "e.g. requests pandas"}
                    value={pkgInput}
                    onChange={e => setPkgInput(e.target.value)}
                    onKeyDown={e => e.key === "Enter" && installDeps()}
                  />
                  <button className="btn-secondary-sm" onClick={installDeps} disabled={installing || !pkgInput.trim()}>
                    {installing ? <Loader size={12} className="spin-slow" /> : <Download size={12} />} Install
                  </button>
                </div>
              )}
              {installResult && (
                <span style={{ fontSize: 11, color: installResult.ok ? "var(--green)" : "var(--red)" }}>
                  {installResult.ok ? "✓ " : "✗ "}{installResult.message}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// -- Run result viewer (real polling, not fake SSE) ----------------
function RunOutput({ scriptId, onClose }) {
  const [runs, setRuns] = useState([]);
  const [polling, setPolling] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    let sawNewRun = false;

    async function trigger() {
      try {
        await api.runFlowScript(scriptId);
      } catch (e) {
        if (!cancelled) { setError(e.message); setPolling(false); }
        return;
      }
      const start = Date.now();
      while (!cancelled && Date.now() - start < 30000) {
        const result = await api.flowScriptRuns(scriptId, 5);
        const list = Array.isArray(result) ? result : (result.runs || []);
        if (list.length) {
          setRuns(list);
          if (list[0].status !== "running" && list[0].status !== "queued") {
            sawNewRun = true;
            break;
          }
        }
        await new Promise(r => setTimeout(r, 800));
      }
      if (!cancelled) {
        setPolling(false);
        if (!sawNewRun) setError("Timed out waiting for the run to finish (30s) -- check history, it may still complete.");
      }
    }
    trigger();
    return () => { cancelled = true; };
  }, [scriptId]);

  const latest = runs[0];

  return (
    <div className="run-output">
      <div className="run-output-header">
        <span>
          {polling ? <><Loader size={12} className="spin-slow" /> Running…</>
            : latest?.status === "success" ? <><CheckCircle size={12} color="#3fb950" /> Success</>
            : <><XCircle size={12} color="#f85149" /> {latest?.status || "Failed"}</>}
        </span>
        <button className="btn-icon" onClick={onClose}><X size={13} /></button>
      </div>
      <div className="run-output-body">
        {error && <div className="flow-error" role="alert">{error}</div>}
        {latest && (
          <>
            {latest.stdout && <div className="run-line stdout"><pre>{latest.stdout}</pre></div>}
            {latest.stderr && <div className="run-line stderr"><pre>{latest.stderr}</pre></div>}
            {!latest.stdout && !latest.stderr && <p className="flow-no-runs">(no output)</p>}
          </>
        )}
      </div>
    </div>
  );
}

// -- Secret Manager (now backed by the real /api/secrets routes) --
function SecretsPanel({ onClose }) {
  const [secretsList, setSecretsList] = useState([]);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [desc, setDesc] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState(null);

  const load = () => api.listSecrets().then(d => setSecretsList(d.secrets || []));
  useEffect(() => { load(); }, []);

  const add = async () => {
    if (!name.trim() || !value.trim()) return;
    setAdding(true);
    setError(null);
    try {
      await api.createSecret(name.trim(), value, desc);
      setName(""); setValue(""); setDesc("");
      load();
    } catch (e) { setError(e.message); }
    finally { setAdding(false); }
  };

  const del = async (id) => {
    if (!window.confirm("Delete this secret?")) return;
    await api.deleteSecret(id);
    load();
  };

  return (
    <div className="secrets-panel">
      <div className="secrets-header">
        <Key size={14} /> <span>Secrets</span>
        <button className="btn-icon" onClick={onClose}><X size={13} /></button>
      </div>
      <p className="secrets-hint">Encrypted at rest, injected as SECRET_&lt;NAME&gt; environment variables in scripts.</p>
      {error && <div className="flow-error" role="alert">{error}</div>}
      <div className="secrets-add">
        <input placeholder="NAME" value={name} onChange={e => setName(e.target.value)} className="secrets-input" />
        <input placeholder="value" type="password" value={value} onChange={e => setValue(e.target.value)} className="secrets-input" />
        <input placeholder="description (opt)" value={desc} onChange={e => setDesc(e.target.value)} className="secrets-input" />
        <button className="btn-primary" onClick={add} disabled={adding || !name || !value}>
          {adding ? <Loader size={12} /> : <Plus size={12} />} Add
        </button>
      </div>
      <div className="secrets-list">
        {secretsList.map(s => (
          <div key={s.id} className="secret-row">
            <span className="secret-name">{s.name}</span>
            <span className="secret-desc">{s.description}</span>
            <button className="btn-danger-icon" onClick={() => del(s.id)}><Trash2 size={11} /></button>
          </div>
        ))}
        {secretsList.length === 0 && <p className="secrets-empty">No secrets yet.</p>}
      </div>
    </div>
  );
}

// -- Chain Graph (visual node editor, custom SVG -- no graph library
//    dependency exists in this project, so this is a lightweight
//    hand-rolled DAG layout: BFS layering + simple column/row placement) --
function ChainGraph({ scripts, chains, onToggle, onDelete }) {
  const layout = useMemo(() => {
    const nodeIds = new Set();
    chains.forEach(c => { nodeIds.add(c.parent_script_id); nodeIds.add(c.child_script_id); });
    const ids = Array.from(nodeIds);
    const incoming = new Map(ids.map(id => [id, 0]));
    const outEdges = new Map(ids.map(id => [id, []]));
    chains.forEach(c => {
      if (outEdges.has(c.parent_script_id)) outEdges.get(c.parent_script_id).push(c);
      if (incoming.has(c.child_script_id)) incoming.set(c.child_script_id, (incoming.get(c.child_script_id) || 0) + 1);
    });
    const level = new Map();
    const roots = ids.filter(id => (incoming.get(id) || 0) === 0);
    const queue = roots.map(id => ({ id, lvl: 0 }));
    const seen = new Set();
    while (queue.length) {
      const { id, lvl } = queue.shift();
      if (seen.has(id) && (level.get(id) || 0) >= lvl) continue;
      seen.add(id);
      level.set(id, Math.max(level.get(id) || 0, lvl));
      (outEdges.get(id) || []).forEach(c => {
        queue.push({ id: c.child_script_id, lvl: lvl + 1 });
      });
    }
    ids.forEach(id => { if (!level.has(id)) level.set(id, 0); });
    const cols = {};
    ids.forEach(id => {
      const lvl = level.get(id) || 0;
      cols[lvl] = cols[lvl] || [];
      cols[lvl].push(id);
    });
    const colW = 200, rowH = 64, padX = 30, padY = 30;
    const pos = {};
    Object.keys(cols).forEach(lvlStr => {
      const lvl = Number(lvlStr);
      cols[lvl].forEach((id, row) => {
        pos[id] = { x: padX + lvl * colW, y: padY + row * rowH };
      });
    });
    const maxLvl = Math.max(0, ...Object.keys(cols).map(Number));
    const maxRows = Math.max(1, ...Object.values(cols).map(arr => arr.length));
    return { ids, pos, width: padX * 2 + (maxLvl + 1) * colW, height: padY * 2 + maxRows * rowH };
  }, [chains]);

  const nameFor = (id) => scripts.find(s => s.id === id)?.name || id.slice(0, 8);

  if (layout.ids.length === 0) {
    return <p className="secrets-empty" style={{ padding: 20 }}>No chains to visualize yet -- add one below.</p>;
  }

  return (
    <div style={{ overflow: "auto", padding: 12 }}>
      <svg width={layout.width} height={layout.height} style={{ minWidth: "100%" }}>
        <defs>
          <marker id="arrow-success" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 Z" fill="#3fb950" />
          </marker>
          <marker id="arrow-failure" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 Z" fill="#f85149" />
          </marker>
        </defs>
        {chains.map(c => {
          const from = layout.pos[c.parent_script_id];
          const to = layout.pos[c.child_script_id];
          if (!from || !to) return null;
          const x1 = from.x + 160, y1 = from.y + 18;
          const x2 = to.x, y2 = to.y + 18;
          const midX = (x1 + x2) / 2;
          const color = c.condition === "on_failure" ? "#f85149" : "#3fb950";
          return (
            <g key={c.id} style={{ cursor: "pointer" }} onClick={() => onToggle(c.id)}>
              <path
                d={`M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}`}
                fill="none"
                stroke={color}
                strokeWidth={c.enabled ? 2 : 1.5}
                strokeDasharray={c.enabled ? "none" : "4,3"}
                opacity={c.enabled ? 1 : 0.5}
                markerEnd={`url(#arrow-${c.condition === "on_failure" ? "failure" : "success"})`}
              />
            </g>
          );
        })}
        {layout.ids.map(id => {
          const p = layout.pos[id];
          if (!p) return null;
          return (
            <g key={id} transform={`translate(${p.x},${p.y})`}>
              <rect width={160} height={36} rx={6} fill="var(--bg-2)" stroke="var(--border)" strokeWidth={1} />
              <text x={80} y={22} textAnchor="middle" fontSize={11} fill="var(--text-1)">
                {nameFor(id).length > 20 ? nameFor(id).slice(0, 18) + "…" : nameFor(id)}
              </text>
            </g>
          );
        })}
      </svg>
      <p style={{ fontSize: 11, color: "var(--text-3)", marginTop: 8 }}>
        <span style={{ color: "#3fb950" }}>green = on success</span> &middot; <span style={{ color: "#f85149" }}>red = on failure</span> &middot; dashed = disabled &middot; click an edge to toggle it
      </p>
    </div>
  );
}

// -- Chain Manager (script chaining / conditional branching) -------
function ChainsPanel({ scripts, onClose }) {
  const [chains, setChains] = useState([]);
  const [parentId, setParentId] = useState("");
  const [childId, setChildId] = useState("");
  const [condition, setCondition] = useState("on_success");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);
  const [view, setView] = useState("list");

  const load = () => api.listChains().then(d => setChains(Array.isArray(d) ? d : (d.chains || []))).catch(e => setError(e.message));
  useEffect(() => { load(); }, []);

  const nameFor = (id) => scripts.find(s => s.id === id)?.name || id;

  const create = async () => {
    if (!parentId || !childId || parentId === childId) return;
    setCreating(true);
    setError(null);
    try {
      await api.createChain(parentId, childId, condition);
      setParentId(""); setChildId("");
      load();
    } catch (e) { setError(e.message); }
    finally { setCreating(false); }
  };

  const toggle = async (chainId) => {
    try { await api.toggleChain(chainId); load(); } catch (e) { setError(e.message); }
  };

  const del = async (chainId) => {
    if (!window.confirm("Delete this chain?")) return;
    try { await api.deleteChain(chainId); load(); } catch (e) { setError(e.message); }
  };

  return (
    <div className="secrets-panel">
      <div className="secrets-header">
        <Link2 size={14} /> <span>Chains</span>
        <div style={{ display: "flex", gap: 4, marginLeft: "auto" }}>
          <button className={`btn-icon ${view === "list" ? "active" : ""}`} title="List view" onClick={() => setView("list")}><List size={13} /></button>
          <button className={`btn-icon ${view === "graph" ? "active" : ""}`} title="Graph view" onClick={() => setView("graph")}><GitBranch size={13} /></button>
        </div>
        <button className="btn-icon" onClick={onClose}><X size={13} /></button>
      </div>
      <p className="secrets-hint">Chain scripts together: run a child script automatically when a parent succeeds or fails.</p>
      {error && <div className="flow-error" role="alert">{error}</div>}
      <div className="secrets-add" style={{ flexWrap: "wrap" }}>
        <select className="secrets-input" value={parentId} onChange={e => setParentId(e.target.value)}>
          <option value="">Parent script...</option>
          {scripts.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select className="secrets-input" value={condition} onChange={e => setCondition(e.target.value)}>
          <option value="on_success">On success</option>
          <option value="on_failure">On failure</option>
        </select>
        <select className="secrets-input" value={childId} onChange={e => setChildId(e.target.value)}>
          <option value="">Child script...</option>
          {scripts.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <button className="btn-primary" onClick={create} disabled={creating || !parentId || !childId || parentId === childId}>
          {creating ? <Loader size={12} /> : <Plus size={12} />} Add Chain
        </button>
      </div>
      {view === "graph" ? (
        <ChainGraph scripts={scripts} chains={chains} onToggle={toggle} onDelete={del} />
      ) : (
        <div className="secrets-list">
          {chains.map(c => (
            <div key={c.id} className="secret-row">
              <span className="secret-name">{nameFor(c.parent_script_id)} → {nameFor(c.child_script_id)}</span>
              <span className="secret-desc">{c.condition === "on_failure" ? "on failure" : "on success"}</span>
              <div className={`toggle ${c.enabled ? "on" : ""}`} onClick={() => toggle(c.id)} title={c.enabled ? "Enabled" : "Disabled"}>
                <div className="toggle-thumb" />
              </div>
              <button className="btn-danger-icon" onClick={() => del(c.id)}><Trash2 size={11} /></button>
            </div>
          ))}
          {chains.length === 0 && <p className="secrets-empty">No chains yet.</p>}
        </div>
      )}
    </div>
  );
}

// -- Main Flow Panel ------------------------------------------------
export default function FlowPanel() {
  const [scripts, setScripts]   = useState([]);
  const [stats, setStats]       = useState(null);
  const [editing, setEditing]   = useState(null);
  const [running, setRunning]   = useState(null);
  const [showSecrets, setShowSecrets] = useState(false);
  const [showChains, setShowChains] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const [runs, setRuns]         = useState({});
  const [error, setError]       = useState(null);

  const load = useCallback(async () => {
    try {
      const [s, st] = await Promise.all([api.flowScripts(), api.flowStats()]);
      setScripts(Array.isArray(s) ? s : []);
      setStats(st);
    } catch (e) { setError(e.message); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const del = async (id) => {
    if (!window.confirm("Delete this script?")) return;
    await api.deleteFlowScript(id);
    load();
  };

  const loadRuns = async (id) => {
    const result = await api.flowScriptRuns(id, 10);
    const list = Array.isArray(result) ? result : (result.runs || []);
    setRuns(r => ({ ...r, [id]: list }));
  };

  const toggleExpand = (id) => {
    if (expanded === id) { setExpanded(null); return; }
    setExpanded(id);
    loadRuns(id);
  };

  if (editing) return (
    <ScriptEditor
      script={editing === "new" ? null : editing}
      onSave={() => { setEditing(null); load(); }}
      onClose={() => setEditing(null)}
    />
  );

  if (running) return (
    <RunOutput scriptId={running} onClose={() => { setRunning(null); load(); }} />
  );

  if (showSecrets) return <SecretsPanel onClose={() => setShowSecrets(false)} />;

  if (showChains) return <ChainsPanel scripts={scripts} onClose={() => setShowChains(false)} />;

  return (
    <div className="flow-panel">
      <div className="flow-header">
        <div className="flow-header-left">
          <span className="flow-logo">Flow</span>
          {stats && <span className="flow-stats">{stats.total} scripts &middot; {stats.enabled} scheduled</span>}
        </div>
        <div className="flow-header-right">
          <button className="btn-icon" title="Chains" onClick={() => setShowChains(true)}><Link2 size={14} /></button>
          <button className="btn-icon" title="Secrets" onClick={() => setShowSecrets(true)}><Key size={14} /></button>
          <button className="btn-icon" title="Refresh" onClick={load}><RefreshCw size={14} /></button>
          <button className="btn-primary" onClick={() => setEditing("new")}><Plus size={13} /> New Script</button>
        </div>
      </div>

      {error && <div className="flow-error" role="alert">{error}</div>}

      <div className="flow-scripts">
        {scripts.length === 0 && !error && (
          <div className="flow-empty">
            <Code size={32} color="#8b949e" />
            <p>No scripts yet</p>
            <p className="flow-empty-hint">Create a script to automate tasks, schedule jobs, and manage secrets.</p>
            <button className="btn-primary" onClick={() => setEditing("new")}><Plus size={13} /> Create your first script</button>
          </div>
        )}

        {scripts.map(s => (
          <div key={s.id} className="flow-script-card">
            <div className="flow-script-main">
              <div className="flow-script-info">
                <span className="flow-script-name">{s.name}</span>
                <span className={`flow-script-status ${s.is_active ? "active" : "inactive"}`}>
                  {s.is_active ? "active" : "inactive"}
                </span>
                {s.schedule_type !== "manual" && (
                  <span className="flow-script-schedule"><Clock size={10} /> {s.schedule_type}</span>
                )}
              </div>
              <div className="flow-script-actions">
                <button className="btn-run" onClick={() => setRunning(s.id)}>
                  <Play size={12} /> Run
                </button>
                <button className="btn-secondary-sm" onClick={() => setEditing(s)}>Edit</button>
                <button className="btn-secondary-sm" onClick={() => toggleExpand(s.id)}>
                  {expanded === s.id ? <ChevronDown size={11} /> : <ChevronRight size={11} />} History
                </button>
                <button className="btn-danger-sm" onClick={() => del(s.id)}><Trash2 size={11} /></button>
              </div>
            </div>

            {expanded === s.id && (
              <div className="flow-runs">
                {!runs[s.id] && <Loader size={12} className="spin-slow" />}
                {runs[s.id]?.length === 0 && <p className="flow-no-runs">No runs yet</p>}
                {runs[s.id]?.map(run => (
                  <div key={run.id} className={`flow-run-row ${run.status}`}>
                    <span className="flow-run-status">
                      {run.status === "success" ? <CheckCircle size={11} color="#3fb950" /> : run.status === "running" ? <Loader size={11} className="spin-slow" /> : <XCircle size={11} color="#f85149" />}
                    </span>
                    <span className="flow-run-trigger">{run.trigger}</span>
                    <span className="flow-run-time">{run.started_at ? new Date(run.started_at).toLocaleString() : ""}</span>
                    {run.duration_ms != null && <span className="flow-run-dur">{run.duration_ms}ms</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
