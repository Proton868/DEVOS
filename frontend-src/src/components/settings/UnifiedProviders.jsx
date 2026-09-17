import React, { useCallback, useEffect, useState } from "react";
import { CheckCircle, AlertCircle, ExternalLink, Save, Loader, Key, Trash2 } from "lucide-react";
import { api } from "../../services/api";
import useStore from "../../store/useStore";

const PROVIDER_LINKS = {
  openrouter: "https://openrouter.ai/keys",
  deepseek: "https://platform.deepseek.com/api_keys",
  gemini: "https://aistudio.google.com/app/apikey",
  huggingface: "https://huggingface.co/settings/tokens",
  openai: "https://platform.openai.com/api-keys",
};

/** Single source of truth for provider rows (no Ollama). */
const PROVIDER_DEFS = [
  {
    id: "omniroute",
    name: "OmniRoute",
    role: "Native DevOS gateway",
    needsUserKey: false,
    systemFields: [
      { key: "OMNIROUTE_BASE_URL", label: "API base URL", placeholder: "http://127.0.0.1:3000/api/v1" },
      { key: "OMNIROUTE_API_KEY", label: "Internal API key (optional)", secret: true },
      { key: "OMNIROUTE_DEFAULT_MODEL", label: "Default model", placeholder: "from catalog" },
    ],
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    role: "Provider / model source",
    needsUserKey: true,
    systemFields: [
      { key: "OPENROUTER_API_KEY", label: "System API key", secret: true },
      { key: "OPENROUTER_BASE_URL", label: "Base URL", placeholder: "https://openrouter.ai/api/v1" },
      { key: "OPENROUTER_DEFAULT_MODEL", label: "Default model" },
    ],
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    role: "Provider",
    needsUserKey: true,
    systemFields: [
      { key: "DEEPSEEK_API_KEY", label: "System API key", secret: true },
      { key: "DEEPSEEK_BASE_URL", label: "Base URL", placeholder: "https://api.deepseek.com" },
      { key: "DEEPSEEK_DEFAULT_MODEL", label: "Default model" },
    ],
  },
  {
    id: "gemini",
    name: "Gemini",
    role: "Provider",
    needsUserKey: true,
    systemFields: [
      { key: "GEMINI_API_KEY", label: "System API key", secret: true },
      { key: "GEMINI_DEFAULT_MODEL", label: "Default model" },
    ],
  },
  {
    id: "openai",
    name: "OpenAI",
    role: "Provider",
    needsUserKey: true,
    systemFields: [
      { key: "OPENAI_API_KEY", label: "System API key", secret: true },
    ],
  },
  {
    id: "huggingface",
    name: "Hugging Face",
    role: "Provider",
    needsUserKey: true,
    systemFields: [
      { key: "HUGGINGFACE_API_KEY", label: "System API key", secret: true },
    ],
  },
  {
    id: "nararouter",
    name: "NaraRouter",
    role: "Provider",
    needsUserKey: true,
    systemFields: [],
  },
];

function fieldValue(cfg, key) {
  const v = cfg?.[key];
  if (v && typeof v === "object" && "masked" in v) return "";
  return typeof v === "string" ? v : "";
}

function fieldConfigured(cfg, key) {
  const v = cfg?.[key];
  if (v && typeof v === "object") return !!v.configured;
  return !!(typeof v === "string" && v);
}

export default function UnifiedProviders() {
  const { providers, selectedProvider, selectedModel, setProvider, setModel } = useStore();
  const [cfg, setCfg] = useState(null);
  const [credStatus, setCredStatus] = useState({});
  const [drafts, setDrafts] = useState({});
  const [sysDrafts, setSysDrafts] = useState({});
  const [busy, setBusy] = useState({});
  const [msg, setMsg] = useState({});
  const [isAdmin, setIsAdmin] = useState(false);
  const [testResult, setTestResult] = useState({});
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [c, ...cred] = await Promise.all([
        api.getProviderConfig().catch(() => ({})),
        ...PROVIDER_DEFS.map((p) =>
          api.getProviderCredentialStatus(p.id).then((r) => [p.id, !!r.credentials_configured]).catch(() => [p.id, false])
        ),
      ]);
      setCfg(c || {});
      setIsAdmin(!!(c && c._meta && c._meta.is_admin));
      const st = {};
      for (const [id, ok] of cred) st[id] = ok;
      setCredStatus(st);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const saveUserKey = async (id) => {
    const key = (drafts[id] || "").trim();
    if (!key) {
      setMsg((m) => ({ ...m, [id]: { type: "err", text: "Enter an API key" } }));
      return;
    }
    setBusy((b) => ({ ...b, [id]: "save" }));
    try {
      await api.putProviderCredential(id, key);
      setDrafts((d) => {
        const n = { ...d };
        delete n[id];
        return n;
      });
      setCredStatus((s) => ({ ...s, [id]: true }));
      setMsg((m) => ({ ...m, [id]: { type: "ok", text: "Credential saved" } }));
    } catch (e) {
      setMsg((m) => ({ ...m, [id]: { type: "err", text: e.message || "Save failed" } }));
    } finally {
      setBusy((b) => ({ ...b, [id]: null }));
    }
  };

  const clearUserKey = async (id) => {
    setBusy((b) => ({ ...b, [id]: "del" }));
    try {
      await api.deleteProviderCredential(id);
      setCredStatus((s) => ({ ...s, [id]: false }));
      setMsg((m) => ({ ...m, [id]: { type: "ok", text: "Credential removed" } }));
    } catch (e) {
      setMsg((m) => ({ ...m, [id]: { type: "err", text: e.message || "Remove failed" } }));
    } finally {
      setBusy((b) => ({ ...b, [id]: null }));
    }
  };

  const saveSystem = async (def) => {
    const updates = {};
    for (const f of def.systemFields) {
      if (sysDrafts[f.key] !== undefined) updates[f.key] = sysDrafts[f.key];
    }
    if (!Object.keys(updates).length) return;
    setBusy((b) => ({ ...b, [def.id]: "sys" }));
    try {
      await api.saveProviderConfig(updates);
      setMsg((m) => ({ ...m, [def.id]: { type: "ok", text: "System config saved" } }));
      await refresh();
    } catch (e) {
      setMsg((m) => ({ ...m, [def.id]: { type: "err", text: e.message || "Save failed" } }));
    } finally {
      setBusy((b) => ({ ...b, [def.id]: null }));
    }
  };

  const testConn = async (id) => {
    setBusy((b) => ({ ...b, [id]: "test" }));
    try {
      const r = await api.testProviderConnection(id);
      setTestResult((t) => ({ ...t, [id]: r }));
    } catch (e) {
      setTestResult((t) => ({ ...t, [id]: { ok: false, error: e.message } }));
    } finally {
      setBusy((b) => ({ ...b, [id]: null }));
    }
  };

  if (loading) return <p className="settings-hint">Loading providers…</p>;

  return (
    <div className="unified-providers">
      <p className="settings-hint">
        Each gateway or provider appears once. OmniRoute is the native DevOS gateway;
        OpenRouter and others are optional sources. Personal keys are encrypted per account.
      </p>
      <div className="provider-list provider-list-unified">
        {PROVIDER_DEFS.map((def) => {
          const runtime = providers?.[def.id] || {};
          const selected = selectedProvider === def.id;
          const models = runtime.models || [];
          const configured = !!runtime.configured || !!credStatus[def.id] || (def.id === "omniroute" && fieldConfigured(cfg, "OMNIROUTE_BASE_URL"));
          return (
            <div
              key={def.id}
              className={`provider-card unified ${selected ? "selected" : ""} ${!configured ? "unconfigured" : ""}`}
            >
              <div
                className="provider-card-header"
                onClick={() => configured && setProvider(def.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if ((e.key === "Enter" || e.key === " ") && configured) setProvider(def.id);
                }}
              >
                <span className="provider-name">{def.name}</span>
                <span className="provider-role">{def.role}</span>
                {configured ? <CheckCircle size={14} color="#4ade80" /> : <AlertCircle size={14} color="#f59e0b" />}
                {PROVIDER_LINKS[def.id] && (
                  <a href={PROVIDER_LINKS[def.id]} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                    <ExternalLink size={12} color="#888" />
                  </a>
                )}
              </div>

              {selected && models.length > 0 && (
                <div className="provider-models">
                  <label className="settings-label">Model</label>
                  <select
                    className="settings-input"
                    value={selectedModel || ""}
                    onChange={(e) => setModel(e.target.value)}
                  >
                    <option value="">Default</option>
                    {models.map((m) => {
                      const id = m.id || m;
                      return (
                        <option key={id} value={id}>
                          {m.name || id}
                        </option>
                      );
                    })}
                  </select>
                </div>
              )}

              {def.needsUserKey && (
                <div className="provider-cred-block">
                  <label className="settings-label">
                    <Key size={12} /> Your API key {credStatus[def.id] ? "(configured)" : ""}
                  </label>
                  <div className="provider-cred-row">
                    <input
                      type="password"
                      className="settings-input"
                      placeholder={credStatus[def.id] ? "•••••••• (enter to replace)" : "Paste API key"}
                      value={drafts[def.id] || ""}
                      onChange={(e) => setDrafts((d) => ({ ...d, [def.id]: e.target.value }))}
                      autoComplete="off"
                    />
                    <button type="button" className="settings-btn" disabled={!!busy[def.id]} onClick={() => saveUserKey(def.id)}>
                      {busy[def.id] === "save" ? <Loader size={12} /> : <Save size={12} />}
                    </button>
                    {credStatus[def.id] && (
                      <button type="button" className="settings-btn" disabled={!!busy[def.id]} onClick={() => clearUserKey(def.id)} title="Remove credential">
                        <Trash2 size={12} />
                      </button>
                    )}
                  </div>
                </div>
              )}

              {isAdmin && def.systemFields.length > 0 && (
                <div className="provider-sys-block">
                  <label className="settings-label">System configuration (admin)</label>
                  {def.systemFields.map((f) => (
                    <div key={f.key} className="provider-config-row">
                      <label>{f.label}</label>
                      <input
                        type={f.secret ? "password" : "text"}
                        className="settings-input"
                        placeholder={
                          f.secret && fieldConfigured(cfg, f.key)
                            ? "•••• configured — enter to replace"
                            : f.placeholder || ""
                        }
                        value={sysDrafts[f.key] ?? (f.secret ? "" : fieldValue(cfg, f.key))}
                        onChange={(e) => setSysDrafts((d) => ({ ...d, [f.key]: e.target.value }))}
                        autoComplete="off"
                      />
                    </div>
                  ))}
                  <button type="button" className="settings-btn" disabled={busy[def.id] === "sys"} onClick={() => saveSystem(def)}>
                    Save system settings
                  </button>
                </div>
              )}

              <div className="provider-actions">
                <button type="button" className="settings-btn" disabled={!!busy[def.id]} onClick={() => testConn(def.id)}>
                  Test connection
                </button>
                {testResult[def.id] && (
                  <span className={`provider-config-test-result ${testResult[def.id].ok ? "ok" : "fail"}`}>
                    {testResult[def.id].ok ? "Connected" : testResult[def.id].error || testResult[def.id].status || "Failed"}
                  </span>
                )}
              </div>
              {msg[def.id] && (
                <p className={`provider-config-test-result ${msg[def.id].type === "ok" ? "ok" : "fail"}`}>{msg[def.id].text}</p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
