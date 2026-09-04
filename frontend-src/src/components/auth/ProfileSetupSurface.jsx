import React, { useState } from "react";
import useStore from "../../store/useStore";
import "./AuthSurfaces.css";

export default function ProfileSetupSurface({ onDone }) {
  const user = useStore((s) => s.user);
  const setUser = useStore((s) => s.setUser);
  const [displayName, setDisplayName] = useState(user?.display_name || user?.username || "");
  const [bio, setBio] = useState(user?.bio || "");
  const [jobTitle, setJobTitle] = useState(user?.job_title || "");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const token = localStorage.getItem("devos_token") || "";
      const r = await fetch("/api/account/profile", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          display_name: displayName.trim() || null,
          bio: bio.trim() || null,
          job_title: jobTitle.trim() || null,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      const next = await r.json();
      setUser(next);
      onDone?.(next);
    } catch (err) {
      setError(err.message || "Could not save profile");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="sp-auth-env" data-surface="profile-setup">
      <div className="sp-auth-ambient" aria-hidden />
      <div className="sp-auth-panel">
        <h1 className="sp-auth-title">Who are you in DevOS?</h1>
        <p className="sp-auth-lead">A display name is enough to continue. You can refine this anytime in Settings.</p>
        {error && <div className="sp-auth-error" role="alert">{error}</div>}
        <form className="sp-auth-form" onSubmit={submit}>
          <label className="sp-auth-label">
            Display name
            <input className="sp-auth-input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={128} />
          </label>
          <label className="sp-auth-label">
            Title (optional)
            <input className="sp-auth-input" value={jobTitle} onChange={(e) => setJobTitle(e.target.value)} maxLength={128} />
          </label>
          <label className="sp-auth-label">
            Bio (optional)
            <textarea className="sp-auth-input sp-auth-textarea" value={bio} onChange={(e) => setBio(e.target.value)} maxLength={2000} rows={3} />
          </label>
          <button type="submit" className="sp-auth-primary" disabled={busy}>
            {busy ? "Saving…" : "Continue"}
          </button>
        </form>
      </div>
    </div>
  );
}
