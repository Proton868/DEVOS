import React, { useState, useEffect, Suspense, lazy } from "react";
import { X, CheckCircle, AlertCircle, ExternalLink, Save, Loader, Search, Download, Package } from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";
import UserProviderCredentials from "./UserProviderCredentials";

// Lazy load ThemeCustomizer to avoid heavy initial bundle
const ThemeCustomizer = lazy(() => import("../settings/ThemeCustomizer"));

const PROVIDER_LINKS = {
  anthropic:"https://console.anthropic.com/keys", openrouter:"https://openrouter.ai/keys",
  deepseek:"https://platform.deepseek.com/api_keys", gemini:"https://aistudio.google.com/app/apikey",
  huggingface:"https://huggingface.co/settings/tokens", ollama:null,
};

// Groups of EDITABLE_PROVIDER_KEYS (core/config.py) into per-provider cards.
const PROVIDER_CONFIG_GROUPS = [
  { id: "default", label: "🎯 Default Provider", fields: [
    { key: "DEFAULT_PROVIDER", label: "Default Provider", placeholder: "ollama" },
  ], testable: false },
  { id: "ollama", label: "🦙 Ollama (local)", fields: [
    { key: "OLLAMA_HOST", label: "Host", placeholder: "http://localhost:11434" },
    { key: "OLLAMA_DEFAULT_MODEL", label: "Default Model", placeholder: "llama3" },
  ], testable: true, testId: "ollama" },
  { id: "openrouter", label: "🌐 OpenRouter", fields: [
    { key: "OPENROUTER_API_KEY", label: "API Key", secret: true },
    { key: "OPENROUTER_BASE_URL", label: "Base URL", placeholder: "https://openrouter.ai/api/v1" },
    { key: "OPENROUTER_DEFAULT_MODEL", label: "Default Model" },
  ], testable: true, testId: "openrouter" },
  { id: "deepseek", label: "🔎 DeepSeek", fields: [
    { key: "DEEPSEEK_API_KEY", label: "API Key", secret: true },
    { key: "DEEPSEEK_BASE_URL", label: "Base URL", placeholder: "https://api.deepseek.com" },
    { key: "DEEPSEEK_DEFAULT_MODEL", label: "Default Model" },
  ], testable: true, testId: "deepseek" },
  { id: "gemini", label: "✨ Gemini", fields: [
    { key: "GEMINI_API_KEY", label: "API Key", secret: true },
    { key: "GEMINI_DEFAULT_MODEL", label: "Default Model", placeholder: "gemini-1.5-flash" },
  ], testable: true, testId: "gemini" },
  { id: "openai", label: "🤖 OpenAI", fields: [
    { key: "OPENAI_API_KEY", label: "API Key", secret: true },
  ], testable: true, testId: "openai" },
  { id: "huggingface", label: "🤗 HuggingFace", fields: [
    { key: "HUGGINGFACE_API_KEY", label: "API Key", secret: true },
    { key: "HUGGINGFACE_BASE_URL", label: "Base URL" },
    { key: "HUGGINGFACE_DEFAULT_MODEL", label: "Default Model" },
  ], testable: true, testId: "huggingface" },
  { id: "nararouter", label: "🧭 NaraRouter", fields: [
    { key: "NARAROUTER_API_KEY", label: "API Key", secret: true },
    { key: "NARAROUTER_BASE_URL", label: "Base URL" },
    { key: "NARAROUTER_DEFAULT_MODEL", label: "Default Model" },
  ], testable: true, testId: "nararouter" },
  { id: "supabase", label: "🗄️ Supabase", fields: [
    { key: "SUPABASE_URL", label: "Project URL", placeholder: "https://xxxx.supabase.co" },
    { key: "SUPABASE_KEY", label: "Anon/Service Key", secret: true },
  ], testable: false },
  { id: "tavily", label: "🔍 Tavily (web search)", fields: [
    { key: "TAVILY_API_KEY", label: "API Key", secret: true },
  ], testable: false },
];

function ProviderConfigEditor() {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [testResults, setTestResults] = useState({});
  const [testing, setTesting] = useState({});

  useEffect(() => {
    api.getProviderConfig().then(setCfg).catch((e) => setError(e.message || "Failed to load provider config"));
  }, []);

  const setField = (key, value) => setCfg((c) => ({ ...c, [key]: value }));

  const saveGroup = async (group) => {
    setSaving(true); setError("");
    try {
      const updates = {};
      group.fields.forEach((f) => { updates[f.key] = cfg[f.key] ?? ""; });
      await api.saveProviderConfig(updates);
      setSaved(true); setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      setError(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async (group) => {
    setTesting((t) => ({ ...t, [group.id]: true }));
    try {
      const result = await api.testProviderConnection(group.testId);
      setTestResults((r) => ({ ...r, [group.id]: result }));
    } catch (e) {
      setTestResults((r) => ({ ...r, [group.id]: { ok: false, error: e.message } }));
    } finally {
      setTesting((t) => ({ ...t, [group.id]: false }));
    }
  };

  if (!cfg) return <p className="settings-hint">Loading provider configuration…</p>;

  return (
    <div>
      <p className="settings-hint">
        Edit API keys, endpoints and default models. Changes are written to the server's <code>.env</code> and applied immediately — no restart needed.
      </p>
      {error && <p className="provider-config-test-result fail">{error}</p>}
      {PROVIDER_CONFIG_GROUPS.map((group) => {
        const result = testResults[group.id];
        return (
          <div key={group.id} className="provider-config-card">
            <div className="provider-config-header">
              <span>{group.label}</span>
              <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                {group.testable && (
                  <button className="btn-secondary-sm" disabled={testing[group.id]} onClick={() => testConnection(group)}>
                    {testing[group.id] ? <Loader size={12} className="spin-slow" /> : "Test"}
                  </button>
                )}
                <button className="btn-secondary-sm" disabled={saving} onClick={() => saveGroup(group)}>
                  <Save size={12} /> Save
                </button>
              </span>
            </div>
            {group.fields.map((f) => (
              <div key={f.key} className="provider-config-row">
                <label>{f.label}</label>
                <input
                  type={f.secret ? "password" : "text"}
                  placeholder={f.placeholder || ""}
                  value={cfg[f.key] ?? ""}
                  onChange={(e) => setField(f.key, e.target.value)}
                />
              </div>
            ))}
            {result && (
              <div className={`provider-config-test-result ${result.ok ? "ok" : "fail"}`}>
                {result.ok ? `✓ Connected — sample reply: "${result.sample}"` : `✗ ${result.error}`}
              </div>
            )}
          </div>
        );
      })}
      {saved && <p className="provider-config-test-result ok">✓ Saved to .env</p>}
    </div>
  );
}

const MARKETPLACE_CATEGORIES = [
  { value: "", label: "All Templates" },
  { value: "productivity", label: "Productivity" },
  { value: "monitoring", label: "Monitoring" },
  { value: "dev-tools", label: "Dev Tools" },
  { value: "backup", label: "Backup" },
  { value: "data", label: "Data" },
  { value: "integration", label: "Integration" },
  { value: "security", label: "Security" },
];

function MarketplacePanel() {
  const [mode, setMode] = useState("templates"); // templates | packages
  const [templates, setTemplates] = useState([]);
  const [category, setCategory] = useState("");
  const [query, setQuery] = useState("");
  const [registry, setRegistry] = useState("npm");
  const [packages, setPackages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [usingId, setUsingId] = useState(null);
  const [usedId, setUsedId] = useState(null);

  const useTemplate = async (t) => {
    setUsingId(t.id);
    setError("");
    try {
      await api.createFlowScript({
        name: t.name,
        description: t.description,
        code: t.code,
        language: t.language,
        schedule_type: t.schedule_type || "manual",
        schedule_value: t.schedule_value || null,
        is_active: false,
      });
      setUsedId(t.id);
      setTimeout(() => setUsedId(null), 2000);
    } catch (e) {
      setError(e.message || "Failed to create script from template");
    } finally {
      setUsingId(null);
    }
  };

  useEffect(() => {
    if (mode === "templates") {
      setLoading(true);
      api.listAutomationTemplates(category || undefined)
        .then(setTemplates)
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false));
    }
  }, [mode, category]);

  const doSearch = async () => {
    if (!query.trim()) return;
    setLoading(true); setError("");
    try {
      const results = await api.searchPackages(query.trim(), registry);
      setPackages(results.results || results || []);
    } catch (e) {
      setError(e.message || "Search failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <p className="settings-hint">
        Browse ready-made automations, or search npm/PyPI for packages to install into a Flow script's environment.
      </p>
      <div className="marketplace-tabs">
        <button className={`marketplace-tab-btn ${mode === "templates" ? "active" : ""}`} onClick={() => setMode("templates")}>
          <Package size={12} style={{ marginRight: 4 }} /> Automation Templates
        </button>
        <button className={`marketplace-tab-btn ${mode === "packages" ? "active" : ""}`} onClick={() => setMode("packages")}>
          <Search size={12} style={{ marginRight: 4 }} /> Search Packages
        </button>
      </div>

      {mode === "templates" && (
        <>
          <SelInput label="Category" value={category} onChange={setCategory} options={MARKETPLACE_CATEGORIES} />
          {loading && <p className="settings-hint">Loading…</p>}
          {error && <p className="provider-config-test-result fail">{error}</p>}
          <div className="marketplace-list">
            {!loading && templates.length === 0 && <div className="marketplace-empty">No templates found.</div>}
            {templates.map((t) => (
              <div key={t.id} className="marketplace-card">
                <div className="marketplace-card-header">
                  <span className="marketplace-card-name">{t.name}</span>
                  <span className="marketplace-card-version">{t.language} · {t.category}</span>
                </div>
                <div className="marketplace-card-desc">{t.description}</div>
                <div className="marketplace-card-actions">
                  {t.packages?.length > 0 && (
                    <span style={{ fontSize: 11, color: "var(--text-3)" }}>
                      Requires: {t.packages.join(", ")}
                    </span>
                  )}
                  <button
                    className="btn-primary-sm"
                    style={{ marginLeft: "auto" }}
                    onClick={() => useTemplate(t)}
                    disabled={usingId === t.id}
                  >
                    {usingId === t.id ? <Loader size={12} className="spin-slow" />
                      : usedId === t.id ? <CheckCircle size={12} />
                      : <Download size={12} />}
                    {usedId === t.id ? " Added to Flow" : " Use Template"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {mode === "packages" && (
        <>
          <div className="marketplace-search-row">
            <select value={registry} onChange={(e) => setRegistry(e.target.value)}>
              <option value="npm">npm</option>
              <option value="pypi">PyPI</option>
            </select>
            <input
              placeholder="Search packages (e.g. axios, requests)…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && doSearch()}
            />
            <button className="btn-primary" onClick={doSearch} disabled={loading}>
              {loading ? <Loader size={13} className="spin-slow" /> : <Search size={13} />}
            </button>
          </div>
          {error && <p className="provider-config-test-result fail">{error}</p>}
          <div className="marketplace-list">
            {!loading && packages.length === 0 && <div className="marketplace-empty">Search {registry === "npm" ? "npm" : "PyPI"} for a package to see results here.</div>}
            {packages.map((p, i) => (
              <div key={p.name || i} className="marketplace-card">
                <div className="marketplace-card-header">
                  <span className="marketplace-card-name">{p.name}</span>
                  <span className="marketplace-card-version">{p.version}</span>
                </div>
                <div className="marketplace-card-desc">{p.description}</div>
                <div className="marketplace-card-actions">
                  <span style={{ fontSize: 11, color: "var(--text-3)" }}>
                    Install from a script's editor (Flow panel) with <Download size={10} style={{ verticalAlign: "-1px" }} /> Install packages.
                  </span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

const Toggle = ({ value, onChange, label }) => (
  <label className="toggle-row">
    <span>{label}</span>
    <div className={`toggle ${value ? "on" : ""}`} onClick={() => onChange(!value)}>
      <div className="toggle-thumb" />
    </div>
  </label>
);

const NumInput = ({ label, value, onChange, min, max, step=1 }) => (
  <label className="settings-row">
    <span>{label}</span>
    <input type="number" className="settings-number" value={value} min={min} max={max} step={step}
      onChange={e => onChange(Number(e.target.value))} />
  </label>
);

const SelInput = ({ label, value, onChange, options }) => (
  <label className="settings-row">
    <span>{label}</span>
    <select className="settings-select" value={value} onChange={e => onChange(e.target.value)}>
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  </label>
);


function AccountPanel({ local, patch, onClose }) {
  const user = useStore((s) => s.user);
  const logout = useStore((s) => s.logout);
  const [me, setMe] = useState(null);
  const [curPw, setCurPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [newPw2, setNewPw2] = useState("");
  const [delPw, setDelPw] = useState("");
  const [delConfirm, setDelConfirm] = useState("");
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(null);

  useEffect(() => {
    api.getMe?.().then(setMe).catch(() => setMe(null));
  }, []);

  const profile = me || user || {};

  const doLogout = async () => {
    try {
      if (logout) await logout();
      else await api.logout?.();
    } catch {}
    onClose?.();
  };

  const changePassword = async () => {
    setMsg(null);
    if (newPw.length < 8) {
      setMsg({ type: "err", text: "New password must be at least 8 characters" });
      return;
    }
    if (newPw !== newPw2) {
      setMsg({ type: "err", text: "New passwords do not match" });
      return;
    }
    setBusy("pw");
    try {
      await api.changePassword(curPw, newPw);
      setCurPw(""); setNewPw(""); setNewPw2("");
      setMsg({ type: "ok", text: "Password updated" });
    } catch (e) {
      setMsg({ type: "err", text: e.message || "Password change failed" });
    } finally {
      setBusy(null);
    }
  };

  const deleteAccount = async () => {
    setMsg(null);
    if (!window.confirm("Permanently delete this account? This cannot be undone.")) return;
    setBusy("del");
    try {
      await api.deleteAccount(delPw, delConfirm || "DELETE");
      if (logout) await logout();
      onClose?.();
    } catch (e) {
      setMsg({ type: "err", text: e.message || "Delete failed" });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="settings-section">
      <h3 className="settings-section-title">Account</h3>
      <p className="settings-hint">Signed-in identity and security controls for this DevOS operator.</p>

      <div className="settings-card" style={{ padding: 12, border: "1px solid var(--border)", borderRadius: 10, marginBottom: 14 }}>
        <div className="settings-row"><span>Username</span><strong>{profile.username || "—"}</strong></div>
        <div className="settings-row"><span>Email</span><strong>{profile.email || "—"}</strong></div>
        <div className="settings-row"><span>Role</span><strong>{profile.is_admin ? "Admin" : "Operator"}</strong></div>
        <div className="settings-row"><span>Identity</span><strong>{profile.supabase_linked ? "Supabase linked" : "Local"}</strong></div>
      </div>

      <label className="settings-label">Display name</label>
      <input
        className="settings-input"
        value={(local.account && local.account.display_name) || ""}
        onChange={(e) => patch("account", "display_name", e.target.value)}
        placeholder="How you appear in the UI"
      />

      <div style={{ marginTop: 18, borderTop: "1px solid var(--border)", paddingTop: 14 }}>
        <h4 className="settings-section-title">Session</h4>
        <button className="btn-secondary" onClick={doLogout}>Log out</button>
      </div>

      <div style={{ marginTop: 18, borderTop: "1px solid var(--border)", paddingTop: 14 }}>
        <h4 className="settings-section-title">Change password</h4>
        <p className="settings-hint">For local accounts only. Supabase-only users should use provider recovery.</p>
        <label className="settings-label">Current password</label>
        <input className="settings-input" type="password" value={curPw} onChange={(e) => setCurPw(e.target.value)} autoComplete="current-password" />
        <label className="settings-label">New password</label>
        <input className="settings-input" type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} autoComplete="new-password" />
        <label className="settings-label">Confirm new password</label>
        <input className="settings-input" type="password" value={newPw2} onChange={(e) => setNewPw2(e.target.value)} autoComplete="new-password" />
        <button className="btn-primary" style={{ marginTop: 8 }} disabled={busy === "pw"} onClick={changePassword}>
          {busy === "pw" ? "Updating…" : "Update password"}
        </button>
      </div>

      <div style={{ marginTop: 18, borderTop: "1px solid var(--border)", paddingTop: 14 }}>
        <h4 className="settings-section-title" style={{ color: "var(--red, #f85149)" }}>Danger zone</h4>
        <p className="settings-hint">Delete permanently deactivates this account and clears the local login password.</p>
        <label className="settings-label">Password (local accounts)</label>
        <input className="settings-input" type="password" value={delPw} onChange={(e) => setDelPw(e.target.value)} />
        <label className="settings-label">Or type DELETE to confirm</label>
        <input className="settings-input" value={delConfirm} onChange={(e) => setDelConfirm(e.target.value)} placeholder="DELETE" />
        <button className="btn-secondary" style={{ marginTop: 8, color: "var(--red, #f85149)", borderColor: "rgba(248,81,73,0.4)" }} disabled={busy === "del"} onClick={deleteAccount}>
          {busy === "del" ? "Deleting…" : "Delete account"}
        </button>
      </div>

      {msg && (
        <p className={`provider-config-test-result ${msg.type === "ok" ? "ok" : "fail"}`} style={{ marginTop: 12 }}>
          {msg.text}
        </p>
      )}
    </div>
  );
}


const SETTINGS_DEFAULTS = {
  account: { display_name: "" },
  ai: {
    autocompleteEnabled: true,
    autocompleteDelay: 400,
    useCodebaseContext: true,
    streamResponses: true,
    temperature: 0.2,
    maxContextFiles: 12,
    agentAutonomy: "supervised",
    showSteelpanBusy: true,
    systemPromptExtra: "",
  },
  editor: { fontSize: 13, tabSize: 2, wordWrap: "on", minimap: false, lineNumbers: "on" },
  git: { autofetch: true, confirmBeforePush: true },
  workspace: { autoSave: true, restoreTabs: true },
  notifications: { hitlToasts: true, runComplete: true, sound: false },
  privacy: { shareTelemetry: false, storeChatHistory: true },
};

function withDefaults(s) {
  const base = s && typeof s === "object" ? s : {};
  const out = { ...base };
  for (const [k, v] of Object.entries(SETTINGS_DEFAULTS)) {
    out[k] = { ...v, ...(base[k] || {}) };
  }
  return out;
}

export default function SettingsModal({ embedded = false, onClose = null }) {
  const { settingsOpen, setSettingsOpen, providers, selectedProvider, selectedModel,
    setProvider, setModel, workspaceSettings, setWorkspaceSettings, theme, setTheme } = useStore();
  const [tab, setTab]   = useState("providers");
  const [local, setLocal] = useState(null);
  const [saved, setSaved] = useState(false);
  const [ucipStats, setUcipStats] = useState(null);
  const active = embedded || settingsOpen;

  const handleClose = () => {
    if (onClose) onClose();
    else setSettingsOpen(false);
  };

  useEffect(() => {
    if (active) {
      if (!local) {
        api.getSettings().then(s => {
          const merged = withDefaults(s && typeof s === "object" ? s : {});
          setLocal(merged);
          setWorkspaceSettings(merged);
        }).catch(() => setLocal(withDefaults({})));
        // Also load model prefs into local.models if category endpoint available
        api.getModelPrefs?.().then((r) => {
          const models = r?.models || {};
          setLocal((prev) => ({ ...(prev || {}), models: { ...(prev?.models || {}), ...models } }));
        }).catch(() => {});
      }
      api.ucipHealth().then(setUcipStats).catch(() => {});
    }
  }, [active]);

  const patch = (section, key, val) => setLocal(s => ({ ...s, [section]: { ...s[section], [key]: val } }));

  const saveAll = async () => {
    if (!local) return;
    try {
      // Persist entire preference bag; backend merges and strips authority fields
      await api.saveSettings(local);
      if (local.models) {
        await api.saveModelPrefs?.(local.models);
      }
      setWorkspaceSettings(local);
      setSaved(true); setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      alert(e.message || "Failed to save settings");
    }
  };

  if (!active) return null;
  const s = local || {};

  const TABS = ["account","providers","models","ai","appearance","workspace","editor","git","notifications","privacy","marketplace","ucip"];

  const inner = (
      <div className={embedded ? "settings-modal wide embedded" : "settings-modal wide"} style={embedded ? { maxWidth: "100%", height: "100%", borderRadius: 0, boxShadow: "none" } : undefined}>
        {!embedded && (
        <div className="settings-header">
          <h2>⚙️ Settings</h2>
          <button onClick={handleClose}><X size={16} /></button>
        </div>
        )}
        <div className="settings-layout">
          <div className="settings-nav">
            {TABS.map(t => (
              <button key={t} className={`settings-nav-item ${tab===t?"active":""}`} onClick={() => setTab(t)}>
                {t === "ucip" ? "🔬 UCIP" : t === "marketplace" ? "🧩 Marketplace" : t.charAt(0).toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>
          <div className="settings-content">

            {tab === "account" && (
              <AccountPanel
                local={local}
                patch={patch}
                onClose={handleClose}
              />
            )}
            {tab === "appearance" && (
              <div className="settings-section">
                <h3 className="settings-section-title">Appearance</h3>
                <label className="settings-label">Theme</label>
                <select className="settings-input" value={(local.appearance&&local.appearance.theme)||theme||"dark"}
                  onChange={(e)=>{ patch("appearance","theme",e.target.value); setTheme(e.target.value); }}>
                  <option value="dark">Dark</option>
                  <option value="light">Light</option>
                  <option value="system">System</option>
                </select>
                <label className="settings-label mt-3">Density</label>
                <select className="settings-input" value={(local.appearance&&local.appearance.density)||"comfortable"}
                  onChange={(e)=>patch("appearance","density",e.target.value)}>
                  <option value="compact">Compact</option>
                  <option value="comfortable">Comfortable</option>
                  <option value="spacious">Spacious</option>
                </select>
              </div>
            )}
            {tab === "models" && (
              <div className="settings-section">
                <h3 className="settings-section-title">Model defaults</h3>
                <p className="settings-hint">Per-user defaults used when a request does not specify a model. Explicit agent/session model always wins.</p>
                {[
                  ["default_chat", "Default chat model"],
                  ["default_coding", "Default coding model"],
                  ["default_reasoning", "Default reasoning model"],
                  ["default_fast", "Default fast model"],
                  ["default_vision", "Default vision model"],
                ].map(([k, label]) => (
                  <div key={k} className="mb-2">
                    <label className="settings-label">{label}</label>
                    <input
                      className="settings-input"
                      list="devos-available-models"
                      value={(local.models && local.models[k]) || ""}
                      onChange={(e) => patch("models", k, e.target.value)}
                      placeholder="Leave blank for system default"
                    />
                  </div>
                ))}
                <datalist id="devos-available-models">
                  {(providers[selectedProvider]?.models || []).map((m) => (
                    <option key={m.id || m} value={m.id || m} />
                  ))}
                </datalist>
              </div>
            )}
            {tab === "providers" && (
              <div>
                <p className="settings-hint">Select the active provider/model below. Your personal API keys are stored encrypted per account.</p>
                <UserProviderCredentials />
                <h4 className="settings-section-title" style={{ marginTop: 20 }}>System provider configuration</h4>
                <p className="settings-hint">Server-wide defaults (admin). Changing these does not replace other users&apos; personal credentials.</p>
                <div className="provider-list">
                  {Object.entries(providers).map(([id, p]) => (
                    <div key={id}
                      className={`provider-card ${selectedProvider===id?"selected":""} ${!p.configured?"unconfigured":""}`}
                      onClick={() => p.configured && setProvider(id)}>
                      <div className="provider-card-header">
                        <span className="provider-icon">{p.icon}</span>
                        <span className="provider-name">{p.name}</span>
                        {p.configured ? <CheckCircle size={14} color="#4ade80"/> : <AlertCircle size={14} color="#f59e0b"/>}
                        {PROVIDER_LINKS[id] && <a href={PROVIDER_LINKS[id]} target="_blank" rel="noreferrer" onClick={e=>e.stopPropagation()}><ExternalLink size={12} color="#888"/></a>}
                      </div>
                      {selectedProvider===id && p.configured && (
                        <div className="provider-models">
                          <label>Model</label>
                          <select value={selectedModel} onChange={e=>setModel(e.target.value)} onClick={e=>e.stopPropagation()}>
                            {p.models.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}
                          </select>
                        </div>
                      )}
                      {!p.configured && <p className="provider-unconfigured-msg">Set <code>{id.toUpperCase()}_API_KEY</code> in <code>.env</code></p>}
                    </div>
                  ))}
                </div>
                <div style={{ marginTop: 20, borderTop: "1px solid var(--border)", paddingTop: 16 }}>
                  <ProviderConfigEditor />
                </div>
              </div>
            )}

            {tab === "marketplace" && <MarketplacePanel />}

            {tab === "editor" && s.editor && (
              <div className="settings-section">
                <NumInput label="Font Size" value={s.editor.fontSize} min={10} max={28} onChange={v=>patch("editor","fontSize",v)}/>
                <NumInput label="Tab Size" value={s.editor.tabSize} min={2} max={8} onChange={v=>patch("editor","tabSize",v)}/>
                <SelInput label="Word Wrap" value={s.editor.wordWrap}
                  options={[{value:"off",label:"Off"},{value:"on",label:"On"}]}
                  onChange={v=>patch("editor","wordWrap",v)}/>
                <SelInput label="Line Numbers" value={s.editor.lineNumbers}
                  options={[{value:"on",label:"On"},{value:"off",label:"Off"},{value:"relative",label:"Relative"}]}
                  onChange={v=>patch("editor","lineNumbers",v)}/>
                <Toggle label="Minimap" value={s.editor.minimap} onChange={v=>patch("editor","minimap",v)}/>
                <Toggle label="Format on Save" value={s.editor.formatOnSave} onChange={v=>patch("editor","formatOnSave",v)}/>
                <Toggle label="Auto Save" value={s.editor.autoSave} onChange={v=>patch("editor","autoSave",v)}/>
              </div>
            )}

            {tab === "ai" && (
              <div className="settings-section">
                <h3 className="settings-section-title">AI behavior</h3>
                <p className="settings-hint">
                  Controls how DevOS uses your connected LLM for chat, agents, and editor assistance.
                  Wire providers under <strong>Providers</strong>; pick defaults under <strong>Models</strong>.
                </p>
                <div className="settings-card" style={{marginBottom:12, padding:12, border:"1px solid var(--border)", borderRadius:10}}>
                  <div className="settings-row"><span>Active provider</span><strong>{selectedProvider || "—"}</strong></div>
                  <div className="settings-row"><span>Active model</span><strong>{selectedModel || "default"}</strong></div>
                  <div className="settings-row"><span>Providers available</span><strong>{(providers||[]).length}</strong></div>
                </div>
                <Toggle label="Inline autocomplete (ghost text)" value={!!(s.ai&&s.ai.autocompleteEnabled)} onChange={v=>patch("ai","autocompleteEnabled",v)}/>
                <NumInput label="Autocomplete delay (ms)" value={(s.ai&&s.ai.autocompleteDelay)||400} min={100} max={3000} step={100} onChange={v=>patch("ai","autocompleteDelay",v)}/>
                <Toggle label="Include codebase context in chat" value={!!(s.ai&&s.ai.useCodebaseContext)} onChange={v=>patch("ai","useCodebaseContext",v)}/>
                <Toggle label="Stream responses" value={s.ai?.streamResponses !== false} onChange={v=>patch("ai","streamResponses",v)}/>
                <Toggle label="Show steelpan when AI is busy" value={s.ai?.showSteelpanBusy !== false} onChange={v=>patch("ai","showSteelpanBusy",v)}/>
                <NumInput label="Max context files" value={(s.ai&&s.ai.maxContextFiles)||12} min={1} max={50} step={1} onChange={v=>patch("ai","maxContextFiles",v)}/>
                <NumInput label="Temperature" value={(s.ai&&s.ai.temperature)!=null?s.ai.temperature:0.2} min={0} max={2} step={0.1} onChange={v=>patch("ai","temperature",v)}/>
                <SelInput label="Agent autonomy" value={(s.ai&&s.ai.agentAutonomy)||"supervised"} onChange={v=>patch("ai","agentAutonomy",v)} options={[
                  {value:"supervised", label:"Supervised (HITL for risky actions)"},
                  {value:"guided", label:"Guided (confirm writes)"},
                  {value:"autonomous", label:"Autonomous (within policy)"},
                ]}/>
                <label className="settings-label" style={{marginTop:8}}>Extra system instructions</label>
                <textarea
                  className="settings-input"
                  rows={4}
                  placeholder="Optional instructions always sent to the model…"
                  value={(s.ai&&s.ai.systemPromptExtra)||""}
                  onChange={(e)=>patch("ai","systemPromptExtra",e.target.value)}
                />
                <p className="settings-hint">If the AI tab looked empty before, defaults are now applied even when the server had no saved AI prefs.</p>
              </div>
            )}

            
            {tab === "workspace" && (
              <div className="settings-section">
                <h3 className="settings-section-title">Workspace</h3>
                <Toggle label="Auto-save files" value={!!(s.workspace&&s.workspace.autoSave)} onChange={v=>patch("workspace","autoSave",v)}/>
                <Toggle label="Restore open tabs on launch" value={s.workspace?.restoreTabs !== false} onChange={v=>patch("workspace","restoreTabs",v)}/>
              </div>
            )}
            {tab === "notifications" && (
              <div className="settings-section">
                <h3 className="settings-section-title">Notifications</h3>
                <Toggle label="HITL approval toasts" value={s.notifications?.hitlToasts !== false} onChange={v=>patch("notifications","hitlToasts",v)}/>
                <Toggle label="Notify when runs complete" value={s.notifications?.runComplete !== false} onChange={v=>patch("notifications","runComplete",v)}/>
                <Toggle label="Sound effects" value={!!(s.notifications&&s.notifications.sound)} onChange={v=>patch("notifications","sound",v)}/>
              </div>
            )}
            {tab === "privacy" && (
              <div className="settings-section">
                <h3 className="settings-section-title">Privacy</h3>
                <Toggle label="Store chat history on server" value={s.privacy?.storeChatHistory !== false} onChange={v=>patch("privacy","storeChatHistory",v)}/>
                <Toggle label="Share anonymous telemetry" value={!!(s.privacy&&s.privacy.shareTelemetry)} onChange={v=>patch("privacy","shareTelemetry",v)}/>
              </div>
            )}

            {tab === "git" && (
              <div className="settings-section">
                <h3 className="settings-section-title">Git</h3>
                <Toggle label="Auto-fetch on open" value={!!(s.git&&s.git.autofetch)} onChange={v=>patch("git","autofetch",v)}/>
                <Toggle label="Confirm before push" value={s.git?.confirmBeforePush !== false} onChange={v=>patch("git","confirmBeforePush",v)}/>
              </div>
            )}

            {tab === "ui" && s.ui && (
              <div className="settings-section">
                <div className="settings-row" style={{ marginBottom: 8 }}>
                  <span>App Theme</span>
                  <div className="theme-switch-group">
                    <button
                      type="button"
                      className={`theme-switch-btn ${theme === "dark" ? "active" : ""}`}
                      onClick={() => setTheme("dark")}
                    >🌙 Dark</button>
                    <button
                      type="button"
                      className={`theme-switch-btn ${theme === "light" ? "active" : ""}`}
                      onClick={() => setTheme("light")}
                    >☀️ Light</button>
                  </div>
                </div>
                <p className="settings-hint" style={{ marginBottom: 12 }}>
                  Switches the whole app's color scheme instantly — separate from the code editor's syntax theme below.
                </p>
                <SelInput label="Editor Syntax Theme" value={s.ui.theme}
                  options={[{value:"vs-dark",label:"Dark"},{value:"light",label:"Light"},{value:"hc-black",label:"High Contrast"}]}
                  onChange={v=>patch("ui","theme",v)}/>
                <NumInput label="Terminal Font Size" value={s.ui.terminalFontSize} min={10} max={24} onChange={v=>patch("ui","terminalFontSize",v)}/>
                <Toggle label="Show Breadcrumbs" value={s.ui.showBreadcrumbs} onChange={v=>patch("ui","showBreadcrumbs",v)}/>
              </div>
            )}

            {tab === "theme" && (
              <div className="settings-section">
                <Suspense fallback={<div className="settings-hint">Loading theme customizer...</div>}>
                  <ThemeCustomizer />
                </Suspense>
              </div>
            )}

            {tab === "ucip" && (
              <div className="settings-section">
                <p className="settings-hint">UCIP v1.0 — Universal Capability Interface Protocol. All platform actions are governed, logged and verifiable.</p>
                {ucipStats && (
                  <div className="ucip-stats-grid">
                    <div className="ucip-stat-box"><span>Status</span><strong style={{color:"#3fb950"}}>{ucipStats.status}</strong></div>
                    <div className="ucip-stat-box"><span>Total Traces</span><strong>{ucipStats.total_traces}</strong></div>
                    <div className="ucip-stat-box"><span>Error Rate</span><strong>{(parseFloat(ucipStats.recent_error_rate)*100).toFixed(1)}%</strong></div>
                    <div className="ucip-stat-box"><span>Version</span><strong>v1.0</strong></div>
                  </div>
                )}
                <p className="settings-hint" style={{marginTop:12}}>
                  UCIP v2 will add: Supabase-persisted traces · policy enforcement engine · multi-agent approval gates · UCIP-S streaming protocol.
                </p>
              </div>
            )}
          </div>
        </div>
        <div className="settings-footer">
          <span>{saved ? "✓ Saved to .carai/settings.json" : "Persisted to workspace"}</span>
          <div style={{display:"flex",gap:8}}>
            <button className="btn-secondary" onClick={handleClose}>Cancel</button>
            <button className="btn-primary" onClick={saveAll}><Save size={13}/> Save</button>
          </div>
        </div>
      </div>
  );
  if (embedded) return inner;
  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) handleClose(); }}>
      {inner}
    </div>
  );
}
