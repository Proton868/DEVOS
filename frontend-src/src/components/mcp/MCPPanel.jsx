import React, { useState, useEffect, useCallback } from "react";
import {
  Plug, Plug2, Trash2, RefreshCw, X, Loader, CheckCircle,
  XCircle, Database, Folder, Github, Wrench, ChevronDown, ChevronRight,
} from "lucide-react";
import { api } from "../../services/api";
import "./MCPPanel.css";

const ICONS = {
  database: Database,
  folder: Folder,
  github: Github,
};

function PresetCard({ preset, connected, onConnect, connecting }) {
  const Icon = ICONS[preset.icon] || Wrench;
  return (
    <div className={`mcp-preset-card ${connected ? "connected" : ""}`}>
      <div className="mcp-preset-icon"><Icon size={18} /></div>
      <div className="mcp-preset-body">
        <div className="mcp-preset-title-row">
          <span className="mcp-preset-name">{preset.label}</span>
          {connected && <span className="mcp-preset-badge connected"><CheckCircle size={11} /> Connected</span>}
          {!preset.ready && !connected && (
            <span className="mcp-preset-badge missing" title={`Missing env: ${preset.missing_env.join(", ")}`}>
              <XCircle size={11} /> Needs config
            </span>
          )}
        </div>
        <p className="mcp-preset-desc">{preset.description}</p>
        {!preset.ready && !connected && (
          <p className="mcp-preset-hint">Set {preset.missing_env.join(", ")} in .env, then reconnect.</p>
        )}
      </div>
      <button
        className={connected ? "btn-danger-sm" : "btn-primary"}
        disabled={connecting || (!preset.ready && !connected)}
        onClick={() => onConnect(preset)}
      >
        {connecting ? <Loader size={12} className="spin-slow" /> : connected ? <Trash2 size={12} /> : <Plug size={12} />}
        {connected ? "Disconnect" : "Connect"}
      </button>
    </div>
  );
}

function CustomServerForm({ onConnect, connecting }) {
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");

  const submit = () => {
    if (!name.trim() || !command.trim()) return;
    onConnect({ id: name.trim(), label: name.trim(), command: command.trim().split(/\s+/) });
  };

  return (
    <div className="mcp-custom-form">
      <input
        className="mcp-input"
        placeholder="Server name (e.g. my-tool)"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <input
        className="mcp-input"
        placeholder="stdio command, e.g. npx -y @scope/my-mcp-server"
        value={command}
        onChange={(e) => setCommand(e.target.value)}
      />
      <button className="btn-primary" disabled={connecting || !name.trim() || !command.trim()} onClick={submit}>
        {connecting ? <Loader size={12} className="spin-slow" /> : <Plug size={12} />} Connect
      </button>
    </div>
  );
}

function ToolsList({ tools }) {
  const [expanded, setExpanded] = useState(false);
  if (!tools.length) return <p className="mcp-empty-hint">No tools discovered yet.</p>;
  return (
    <div className="mcp-tools-list">
      <button className="mcp-tools-toggle" onClick={() => setExpanded((e) => !e)}>
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {tools.length} tool{tools.length === 1 ? "" : "s"} discovered
      </button>
      {expanded && (
        <div className="mcp-tools-grid">
          {tools.map((t) => (
            <div key={t._prefixed_name || t.name} className="mcp-tool-chip" title={t.description || ""}>
              <Wrench size={10} /> {t.name}
              <span className="mcp-tool-server">{t._server}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function MCPPanel({ onClose }) {
  const [presets, setPresets] = useState([]);
  const [servers, setServers] = useState([]);
  const [tools, setTools] = useState([]);
  const [loading, setLoading] = useState(true);
  const [connectingId, setConnectingId] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const [p, s, t] = await Promise.all([
        api.listMcpPresets(),
        api.listServers(),
        api.listTools(),
      ]);
      setPresets(p.presets || []);
      setServers(s.servers || []);
      setTools(t.tools || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const connectedNames = new Set(servers.map((s) => s.name));

  const handlePresetConnect = async (preset) => {
    setError(null);
    setConnectingId(preset.id);
    try {
      if (connectedNames.has(preset.id)) {
        await api.disconnectServer(preset.id);
      } else {
        await api.connectServer(preset.id, preset.command);
      }
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setConnectingId(null);
    }
  };

  const handleCustomConnect = async ({ id, command }) => {
    setError(null);
    setConnectingId(id);
    try {
      await api.connectServer(id, command);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setConnectingId(null);
    }
  };

  return (
    <div className="mcp-panel">
      <div className="mcp-header">
        <Plug2 size={15} />
        <span className="mcp-title">MCP Connections</span>
        <span className="mcp-subtitle">{servers.length} connected</span>
        <button className="btn-icon" title="Refresh" onClick={load}><RefreshCw size={13} /></button>
        {onClose && <button className="btn-icon" onClick={onClose}><X size={14} /></button>}
      </div>

      <p className="mcp-intro">
        Connect DevOS to external tools over the Model Context Protocol — give agents
        live access to Supabase, your filesystem, GitHub, or any custom MCP server.
      </p>

      {error && <div className="flow-error" role="alert">{error}</div>}

      {loading ? (
        <div className="mcp-loading"><Loader size={16} className="spin-slow" /> Loading...</div>
      ) : (
        <>
          <div className="mcp-presets">
            {presets.map((preset) => (
              <PresetCard
                key={preset.id}
                preset={preset}
                connected={connectedNames.has(preset.id)}
                connecting={connectingId === preset.id}
                onConnect={handlePresetConnect}
              />
            ))}
          </div>

          <div className="mcp-section-label">Custom MCP server</div>
          <CustomServerForm onConnect={handleCustomConnect} connecting={!!connectingId} />

          <div className="mcp-section-label">Discovered tools</div>
          <ToolsList tools={tools} />
        </>
      )}
    </div>
  );
}
