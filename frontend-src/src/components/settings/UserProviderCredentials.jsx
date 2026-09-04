import React, { useEffect, useState, useCallback } from "react";
import { Loader, CheckCircle, AlertCircle, Key, Trash2 } from "lucide-react";
import { api } from "../../services/api";

/** Providers a normal user may attach their own credentials to. */
const USER_PROVIDERS = [
  { id: "openrouter", label: "OpenRouter", needsKey: true },
  { id: "openai", label: "OpenAI", needsKey: true },
  { id: "deepseek", label: "DeepSeek", needsKey: true },
  { id: "gemini", label: "Gemini", needsKey: true },
  { id: "huggingface", label: "Hugging Face", needsKey: true },
  { id: "nararouter", label: "NaraRouter", needsKey: true },
  { id: "ollama", label: "Ollama (local)", needsKey: false },
];

/**
 * My provider credentials — encrypted server-side via
 * PUT/DELETE /api/models/providers/{id}/credential.
 * Never persists the key in React state beyond the controlled input,
 * and never writes it to localStorage.
 */
export default function UserProviderCredentials() {
  const [status, setStatus] = useState({}); // providerId -> boolean
  const [drafts, setDrafts] = useState({}); // providerId -> plaintext (ephemeral)
  const [busy, setBusy] = useState({});
  const [msg, setMsg] = useState({});
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    const next = {};
    await Promise.all(
      USER_PROVIDERS.map(async (p) => {
        try {
          const r = await api.getProviderCredentialStatus(p.id);
          next[p.id] = !!r.credentials_configured;
        } catch {
          next[p.id] = false;
        }
      })
    );
    setStatus(next);
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const setDraft = (id, value) => {
    setDrafts((d) => ({ ...d, [id]: value }));
  };

  const save = async (id) => {
    const key = (drafts[id] || "").trim();
    if (!key) {
      setMsg((m) => ({ ...m, [id]: { type: "err", text: "Enter an API key" } }));
      return;
    }
    setBusy((b) => ({ ...b, [id]: "save" }));
    setMsg((m) => ({ ...m, [id]: null }));
    try {
      await api.putProviderCredential(id, key);
      // Clear ephemeral draft immediately — do not retain the key
      setDrafts((d) => {
        const n = { ...d };
        delete n[id];
        return n;
      });
      setStatus((s) => ({ ...s, [id]: true }));
      setMsg((m) => ({ ...m, [id]: { type: "ok", text: "Configured ✓" } }));
    } catch (e) {
      setMsg((m) => ({ ...m, [id]: { type: "err", text: e.message || "Save failed" } }));
    } finally {
      setBusy((b) => ({ ...b, [id]: null }));
    }
  };

  const remove = async (id) => {
    if (!window.confirm(`Remove your ${id} credential from this account?`)) return;
    setBusy((b) => ({ ...b, [id]: "del" }));
    try {
      await api.deleteProviderCredential(id);
      setStatus((s) => ({ ...s, [id]: false }));
      setDrafts((d) => {
        const n = { ...d };
        delete n[id];
        return n;
      });
      setMsg((m) => ({ ...m, [id]: { type: "ok", text: "Removed" } }));
    } catch (e) {
      setMsg((m) => ({ ...m, [id]: { type: "err", text: e.message || "Remove failed" } }));
    } finally {
      setBusy((b) => ({ ...b, [id]: null }));
    }
  };

  const test = async (id) => {
    setBusy((b) => ({ ...b, [id]: "test" }));
    try {
      const r = await api.testProviderConnection(id);
      setMsg((m) => ({
        ...m,
        [id]: r.ok
          ? { type: "ok", text: r.sample ? `Connection OK — "${r.sample}"` : "Connection OK" }
          : { type: "err", text: [r.error, r.hint].filter(Boolean).join(" — ") || "Connection failed" },
      }));
    } catch (e) {
      setMsg((m) => ({ ...m, [id]: { type: "err", text: e.message || "Test failed" } }));
    } finally {
      setBusy((b) => ({ ...b, [id]: null }));
    }
  };

  if (loading) {
    return <p className="settings-hint">Loading credential status…</p>;
  }

  return (
    <div className="user-provider-credentials">
      <h4 className="settings-section-title" style={{ marginTop: 4 }}>My provider credentials</h4>
      <p className="settings-hint">
        Keys are encrypted on the server and never shown again after save.
        They apply only to your account — not to other users or system defaults.
      </p>
      {USER_PROVIDERS.map((p) => {
        const configured = !!status[p.id];
        const b = busy[p.id];
        const m = msg[p.id];
        return (
          <div key={p.id} className="provider-config-card" style={{ marginBottom: 10 }}>
            <div className="provider-config-header" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Key size={14} />
              <span style={{ fontWeight: 600 }}>{p.label}</span>
              <span
                className="text-xs"
                style={{
                  marginLeft: "auto",
                  color: configured ? "var(--success, #4ade80)" : "var(--muted, #94a3b8)",
                }}
              >
                {configured ? (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                    <CheckCircle size={12} /> Configured
                  </span>
                ) : (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                    <AlertCircle size={12} /> Not configured
                  </span>
                )}
              </span>
            </div>

            {p.needsKey && (
              <div style={{ marginTop: 8 }}>
                <label className="settings-label">
                  {configured ? "Replace API key" : "API key"}
                </label>
                <input
                  type="password"
                  autoComplete="off"
                  className="settings-input"
                  placeholder={configured ? "•••••••• (enter new key to replace)" : "sk-…"}
                  value={drafts[p.id] || ""}
                  onChange={(e) => setDraft(p.id, e.target.value)}
                />
              </div>
            )}

            <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
              {p.needsKey && (
                <button
                  className="btn-primary-sm"
                  disabled={!!b}
                  onClick={() => save(p.id)}
                >
                  {b === "save" ? <Loader size={12} className="spin-slow" /> : configured ? "Replace" : "Save"}
                </button>
              )}
              <button className="btn-secondary-sm" disabled={!!b} onClick={() => test(p.id)}>
                {b === "test" ? <Loader size={12} className="spin-slow" /> : "Test"}
              </button>
              {configured && p.needsKey && (
                <button
                  className="btn-secondary-sm"
                  disabled={!!b}
                  onClick={() => remove(p.id)}
                  style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
                >
                  {b === "del" ? <Loader size={12} className="spin-slow" /> : <Trash2 size={12} />}
                  Remove
                </button>
              )}
            </div>
            {m && (
              <p
                className={`provider-config-test-result ${m.type === "ok" ? "ok" : "fail"}`}
                style={{ marginTop: 8 }}
              >
                {m.text}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
