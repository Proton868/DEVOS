import React, { useState, useEffect, Suspense, lazy } from "react";
import { X, CheckCircle, AlertCircle, ExternalLink, Save, Loader, Search, Download, Package } from "lucide-react";
import useStore from "../../store/useStore";
import { api } from "../../services/api";

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

export default function SettingsModal() {
  const { settingsOpen, setSettingsOpen, providers, selectedProvider, selectedModel,
    setProvider, setModel, workspaceSettings, setWorkspaceSettings, theme, setTheme } = useStore();
  const [tab, setTab]   = useState("providers");
  const [local, setLocal] = useState(null);
  const [saved, setSaved] = useState(false);
  const [ucipStats, setUcipStats] = useState(null);

  useEffect(() => {
    if (settingsOpen) {
      if (!local) api.getSettings().then(s => { setLocal(s); setWorkspaceSettings(s); }).catch(() => {});
      api.ucipHealth().then(setUcipStats).catch(() => {});
    }
  }, [settingsOpen]);

  const patch = (section, key, val) => setLocal(s => ({ ...s, [section]: { ...s[section], [key]: val } }));

  const saveAll = async () => {
    if (!local) return;
    await api.saveSettings(local);
    setWorkspaceSettings(local);
    setSaved(true); setTimeout(() => setSaved(false), 1500);
  };

  if (!settingsOpen) return null;
  const s = local || {};

  const TABS = ["providers","editor","ai","git","ui","theme","marketplace","ucip"];

  return (
    <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) setSettingsOpen(false); }}>
      <div className="settings-modal wide">
        <div className="settings-header">
          <h2>⚙️ Settings</h2>
          <button onClick={() => setSettingsOpen(false)}><X size={16} /></button>
        </div>
        <div className="settings-layout">
          <div className="settings-nav">
            {TABS.map(t => (
              <button key={t} className={`settings-nav-item ${tab===t?"active":""}`} onClick={() => setTab(t)}>
                {t === "ucip" ? "🔬 UCIP" : t === "marketplace" ? "🧩 Marketplace" : t.charAt(0).toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>
          <div className="settings-content">

            {tab === "providers" && (
              <div>
                <p className="settings-hint">Select the active provider/model below, or edit credentials and endpoints directly.</p>
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

            {tab === "ai" && s.ai && (
              <div className="settings-section">
                <Toggle label="Inline Autocomplete" value={s.ai.autocompleteEnabled} onChange={v=>patch("ai","autocompleteEnabled",v)}/>
                <NumInput label="Autocomplete Delay (ms)" value={s.ai.autocompleteDelay} min={100} max={3000} step={100} onChange={v=>patch("ai","autocompleteDelay",v)}/>
                <Toggle label="Codebase Context in Chat" value={s.ai.useCodebaseContext} onChange={v=>patch("ai","useCodebaseContext",v)}/>
              </div>
            )}

            {tab === "git" && s.git && (
              <div className="settings-section">
                <Toggle label="Auto-fetch on open" value={s.git.autofetch} onChange={v=>patch("git","autofetch",v)}/>
                <Toggle label="Confirm before push" value={s.git.confirmBeforePush} onChange={v=>patch("git","confirmBeforePush",v)}/>
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
            <button className="btn-secondary" onClick={()=>setSettingsOpen(false)}>Cancel</button>
            <button className="btn-primary" onClick={saveAll}><Save size={13}/> Save</button>
          </div>
        </div>
      </div>
    </div>
  );
}
