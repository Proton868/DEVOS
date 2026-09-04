/**
 * Persona profile — XP, rename, provider/model, learning (spatial overlay).
 * XP is informational only; does not grant UCIP authority.
 */
import React, { useEffect, useState } from "react";
import { X } from "lucide-react";
import useOsStore from "./store/osStore";
import useStore from "../store/useStore";
import { api } from "../services/api";

export default function PersonaProfilePanel() {
  const personaId = useOsStore((s) => s.personaProfileOpen) || "nuha";
  const close = useOsStore((s) => s.closePersonaProfile);
  const setActivePersona = useOsStore((s) => s.setActivePersona);
  const openCopilot = useOsStore((s) => s.openCopilot);
  const providers = useStore((s) => s.providers) || [];
  const [profile, setProfile] = useState(null);
  const [events, setEvents] = useState([]);
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [p, exp] = await Promise.all([
        api.getPersonaProfile(personaId),
        api.getPersonaExperience(personaId).catch(() => ({ events: [] })),
      ]);
      setProfile(p);
      setName(p.display_name || "");
      setProvider(p.provider || "");
      setModel(p.model || "");
      setEvents(exp.events || []);
    } catch (e) {
      setMsg({ type: "err", text: e.message || "Failed to load profile" });
    }
  };

  useEffect(() => {
    load();
  }, [personaId]);

  const save = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const p = await api.patchPersonaProfile(personaId, {
        display_name: name,
        provider: provider || null,
        model: model || null,
      });
      setProfile(p);
      setMsg({ type: "ok", text: "Saved" });
    } catch (e) {
      setMsg({ type: "err", text: e.message || "Save failed" });
    } finally {
      setBusy(false);
    }
  };

  const talk = () => {
    setActivePersona(personaId);
    openCopilot(null, null, personaId);
    close();
  };

  if (!profile) {
    return (
      <div className="sp-persona-profile">
        <div className="sp-persona-head">
          <span>Persona</span>
          <button className="sp-iconbtn" onClick={close}><X size={15} /></button>
        </div>
        <p className="settings-hint" style={{ padding: 16 }}>Loading…</p>
      </div>
    );
  }

  const pct = Math.round((profile.progress || 0) * 100);

  return (
    <div className="sp-persona-profile">
      <div className="sp-persona-head">
        <span>◉ {profile.display_name}</span>
        <button className="sp-iconbtn" onClick={close}><X size={15} /></button>
      </div>
      <div className="sp-persona-body">
        <div className="sp-persona-hero">
          <div className="sp-persona-name">{profile.display_name}</div>
          <div className="sp-persona-role">{profile.role || "specialist"} · {profile.specialty || "—"}</div>
          <div className="sp-persona-bar">
            <i style={{ width: `${pct}%` }} />
          </div>
          <div className="sp-persona-xp">
            Level {profile.level} · {profile.xp} / {profile.level_ceiling} XP
            <span className="sp-persona-xp-next"> · {profile.xp_to_next_level} to next</span>
          </div>
          <p className="settings-hint" style={{ marginTop: 8 }}>{profile.authority_note}</p>
        </div>

        <div className="sp-persona-stats">
          <div><b>{profile.tasks_completed}</b><span>Tasks</span></div>
          <div><b>{profile.verified_outcomes}</b><span>Verified</span></div>
          <div><b>{profile.delegations_successful}</b><span>Delegations</span></div>
        </div>

        <label className="settings-label">Display name</label>
        <input className="settings-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Stable id unchanged" />

        <label className="settings-label">Provider</label>
        <select className="settings-input" value={provider} onChange={(e) => setProvider(e.target.value)}>
          <option value="">System default</option>
          {(providers.length ? providers : ["ollama", "openrouter", "deepseek", "openai", "gemini"]).map((pr) => {
            const id = typeof pr === "string" ? pr : pr.id || pr.name;
            return <option key={id} value={id}>{id}</option>;
          })}
        </select>

        <label className="settings-label">Model</label>
        <input className="settings-input" value={model} onChange={(e) => setModel(e.target.value)} placeholder="Optional model id" />

        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <button className="btn-primary-sm" disabled={busy} onClick={save}>{busy ? "Saving…" : "Save"}</button>
          <button className="btn-secondary-sm" onClick={talk}>Talk to {profile.display_name}</button>
        </div>
        {msg && (
          <p className={`provider-config-test-result ${msg.type === "ok" ? "ok" : "fail"}`}>{msg.text}</p>
        )}

        <h4 className="settings-section-title" style={{ marginTop: 16 }}>Learning</h4>
        {(profile.learning_events || []).length === 0 && (
          <p className="settings-hint">No learning events yet.</p>
        )}
        <ul className="sp-persona-list">
          {(profile.learning_events || []).slice(0, 8).map((le, i) => (
            <li key={i}>✓ {le.text || le}</li>
          ))}
        </ul>

        <h4 className="settings-section-title">Accomplishments</h4>
        {(profile.accomplishments || []).length === 0 && (
          <p className="settings-hint">None yet — verified work unlocks these.</p>
        )}
        <ul className="sp-persona-list">
          {(profile.accomplishments || []).map((a) => (
            <li key={a.id || a.title}>✦ {a.title || a.id}</li>
          ))}
        </ul>

        <h4 className="settings-section-title">Recent XP</h4>
        {events.length === 0 && <p className="settings-hint">No experience events yet.</p>}
        <ul className="sp-persona-list">
          {events.slice(0, 12).map((e) => (
            <li key={e.id}>+{e.xp} {e.event_type} — {e.reason}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
