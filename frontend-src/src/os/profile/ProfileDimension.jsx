/**
 * Professional Profile — first-class identity surface (Spatial OS).
 * Uses existing /api/account profile + avatar endpoints (no duplicate storage).
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { X, Camera, Save, User } from "lucide-react";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api, getToken } from "../../services/api";
import ProfileAvatar from "./ProfileAvatar";
import { resolveProfileLayout } from "./profileLayout";

const AVATAR_ACCEPT = "image/png,image/jpeg,image/webp,image/gif";
const AVATAR_MAX = 2_000_000;

export default function ProfileDimension() {
  const overlay = useOsStore((s) => s.overlay);
  const setOverlay = useOsStore((s) => s.setOverlay);
  const user = useStore((s) => s.user);
  const setUser = useStore((s) => s.setUser);

  const open = overlay === "profile";
  const rootRef = useRef(null);
  const fileRef = useRef(null);
  const [size, setSize] = useState({ width: 800, height: 600 });
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [avatarVer, setAvatarVer] = useState(0);
  const [form, setForm] = useState({
    display_name: "",
    username: "",
    bio: "",
    job_title: "",
    organization: "",
    status_message: "",
    skills: "",
  });
  const [section, setSection] = useState("identity"); // identity | work | activity

  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (cr) setSize({ width: cr.width, height: cr.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [open]);

  const load = useCallback(async () => {
    if (!open) return;
    setLoading(true);
    setError(null);
    try {
      const token = getToken();
      const r = await fetch("/api/account/profile/summary", {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      const data = r.ok ? await r.json() : null;
      const base = data || user || {};
      setSummary(base);
      setForm({
        display_name: base.display_name || "",
        username: base.username || "",
        bio: base.bio || "",
        job_title: base.job_title || "",
        organization: base.organization || "",
        status_message: base.status_message || "",
        skills: base.skills || (base.skills_list || []).join(", "),
      });
    } catch (e) {
      setError(e.message || "Failed to load profile");
    } finally {
      setLoading(false);
    }
  }, [open, user]);

  useEffect(() => {
    load();
  }, [load]);

  const layout = resolveProfileLayout(size);

  if (!open) return null;

  const onSave = async () => {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const token = getToken();
      const r = await fetch("/api/account/profile", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          display_name: form.display_name.trim() || null,
          bio: form.bio.trim() || null,
          job_title: form.job_title.trim() || null,
          organization: form.organization.trim() || null,
          status_message: form.status_message.trim() || null,
          skills: form.skills.trim() || null,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      const next = await r.json();
      setUser(next);
      setSummary((s) => ({ ...(s || {}), ...next }));
      setNotice("Profile saved");
    } catch (e) {
      setError(e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const onAvatar = async (file) => {
    if (!file) return;
    setUploading(true);
    setError(null);
    setNotice(null);
    try {
      if (!AVATAR_ACCEPT.split(",").includes(file.type) && !file.type.startsWith("image/")) {
        throw new Error("Unsupported image type. Use PNG, JPEG, WebP, or GIF.");
      }
      if (file.size > AVATAR_MAX) {
        throw new Error("Avatar must be 2MB or smaller.");
      }
      const fd = new FormData();
      fd.append("file", file);
      const token = getToken();
      const r = await fetch("/api/account/avatar", {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(t || "Upload failed");
      }
      const next = await r.json();
      setUser(next);
      setSummary((s) => ({ ...(s || {}), ...next }));
      setAvatarVer((v) => v + 1);
      setNotice(file.type === "image/gif" ? "Animated avatar uploaded" : "Avatar updated");
    } catch (e) {
      setError(e.message || "Upload failed");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const display = form.display_name || form.username || "Developer";
  const avatarSrc = summary?.avatar_url || user?.avatar_url || "/api/account/avatar";
  const stats = summary?.stats || {};

  const identityBlock = (
    <section className="sp-profile-card">
      <h3>Identity</h3>
      <div className="sp-profile-identity">
        <div className="sp-profile-avatar-wrap">
          <ProfileAvatar src={avatarSrc} name={display} size={layout.density === "compact" ? 72 : 96} version={avatarVer} />
          <button
            type="button"
            className="sp-profile-camera"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
            title="Upload avatar (PNG, JPEG, WebP, GIF)"
          >
            <Camera size={14} />
          </button>
          <input
            ref={fileRef}
            type="file"
            accept={AVATAR_ACCEPT}
            hidden
            onChange={(e) => onAvatar(e.target.files?.[0])}
          />
        </div>
        <div className="sp-profile-fields">
          <label>
            Display name
            <input
              value={form.display_name}
              maxLength={128}
              onChange={(e) => setForm((f) => ({ ...f, display_name: e.target.value }))}
            />
          </label>
          <label>
            Username
            <input value={form.username} disabled title="Username is account-scoped" />
          </label>
          <label>
            Status
            <input
              value={form.status_message}
              maxLength={160}
              placeholder="What are you focused on?"
              onChange={(e) => setForm((f) => ({ ...f, status_message: e.target.value }))}
            />
          </label>
        </div>
      </div>
    </section>
  );

  const workBlock = (
    <section className="sp-profile-card">
      <h3>Professional</h3>
      <label>
        Title
        <input
          value={form.job_title}
          maxLength={128}
          onChange={(e) => setForm((f) => ({ ...f, job_title: e.target.value }))}
        />
      </label>
      <label>
        Organization
        <input
          value={form.organization}
          maxLength={128}
          onChange={(e) => setForm((f) => ({ ...f, organization: e.target.value }))}
        />
      </label>
      <label>
        Bio
        <textarea
          value={form.bio}
          maxLength={2000}
          rows={layout.density === "compact" ? 3 : 4}
          onChange={(e) => setForm((f) => ({ ...f, bio: e.target.value }))}
        />
      </label>
      <label>
        Skills / technologies
        <input
          value={form.skills}
          maxLength={1024}
          placeholder="Python, React, Postgres, …"
          onChange={(e) => setForm((f) => ({ ...f, skills: e.target.value }))}
        />
      </label>
      {form.skills && (
        <div className="sp-profile-tags">
          {form.skills.split(",").map((s) => s.trim()).filter(Boolean).slice(0, 24).map((s) => (
            <span key={s} className="sp-profile-tag">{s}</span>
          ))}
        </div>
      )}
    </section>
  );

  const activityBlock = (
    <section className="sp-profile-card">
      <h3>Workspace</h3>
      <div className="sp-profile-stats">
        <div><strong>{stats.projects ?? "—"}</strong><span>Projects</span></div>
        <div><strong>{stats.workflows ?? "—"}</strong><span>Workflows</span></div>
        <div><strong>{summary?.plan || user?.plan || "—"}</strong><span>Plan</span></div>
        <div><strong>{summary?.role || user?.role || "—"}</strong><span>Role</span></div>
      </div>
      <p className="sp-profile-hint">
        Agents, workflows, and activity stay tied to your authenticated workspace.
        This profile does not grant capabilities — UCIP remains authoritative.
      </p>
    </section>
  );

  return (
    <div className="sp-profile-dim-root" ref={rootRef} data-profile-density={layout.density}>
      <div className="sp-profile-dim" role="dialog" aria-label="Professional profile">
        <header className="sp-profile-head">
          <div className="sp-profile-head-title">
            <User size={16} />
            <span>Profile</span>
            {form.status_message ? <span className="sp-profile-status-pill">{form.status_message}</span> : null}
          </div>
          <div className="sp-profile-head-actions">
            <button type="button" className="sp-pv-btn" disabled={saving} onClick={onSave}>
              <Save size={14} />
              <span>{saving ? "Saving…" : "Save"}</span>
            </button>
            <button type="button" className="sp-iconbtn" title="Close" onClick={() => setOverlay(null)}>
              <X size={16} />
            </button>
          </div>
        </header>

        {error && <div className="sp-profile-banner sp-profile-banner--err" role="alert">{error}</div>}
        {notice && <div className="sp-profile-banner" role="status">{notice}</div>}
        {loading && <div className="sp-profile-banner">Loading profile…</div>}
        {uploading && <div className="sp-profile-banner">Uploading avatar…</div>}

        {layout.sectionsAsDrawers && (
          <nav className="sp-profile-tabs" aria-label="Profile sections">
            {["identity", "work", "activity"].map((id) => (
              <button
                key={id}
                type="button"
                className={section === id ? "active" : ""}
                onClick={() => setSection(id)}
              >
                {id}
              </button>
            ))}
          </nav>
        )}

        <div className={`sp-profile-body ${layout.columns === 2 ? "sp-profile-body--split" : ""}`}>
          {(!layout.sectionsAsDrawers || section === "identity") && identityBlock}
          {(!layout.sectionsAsDrawers || section === "work") && workBlock}
          {(!layout.sectionsAsDrawers || section === "activity") && activityBlock}
        </div>
      </div>
    </div>
  );
}
