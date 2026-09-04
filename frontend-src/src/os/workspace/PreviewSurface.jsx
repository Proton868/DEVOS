/**
 * Workspace Preview — spatial surface for verified HTML/CSS/JS artifacts.
 * Presentation only: does not authorize UCIP or run missions.
 *
 * Sandbox policy (do not loosen without security review):
 *   allow-scripts      — needed for JS/CSS-driven pages; scripts run in opaque origin
 *   (no allow-same-origin) — prevents same-origin access to DevOS APIs/DOM/cookies
 *   (no allow-forms / allow-popups / allow-top-navigation)
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import useOsStore from "../store/osStore";
import { getToken, baseUrl } from "../../services/api";

function currentProjectId(explicit) {
  if (explicit) return explicit;
  try {
    return localStorage.getItem("devos_current_project") || "default";
  } catch {
    return "default";
  }
}

export function buildPreviewUrl(projectId, path, previewToken) {
  const pid = encodeURIComponent(projectId || "default");
  const clean = (path || "index.html").replace(/^\/+/, "");
  const base = `/api/files/${pid}/preview/${clean.split("/").map(encodeURIComponent).join("/")}`;
  if (previewToken) return `${base}?token=${encodeURIComponent(previewToken)}`;
  return base;
}

/** Iframe sandbox: scripts yes, same-origin no (opaque unique origin). */
export const PREVIEW_IFRAME_SANDBOX = "allow-scripts";

export default function PreviewSurface() {
  const preview = useOsStore((s) => s.preview);
  const closePreview = useOsStore((s) => s.closePreview);
  const minimizePreview = useOsStore((s) => s.minimizePreview);
  const restorePreview = useOsStore((s) => s.restorePreview);
  const setPreviewError = useOsStore((s) => s.setPreviewError);

  const [nonce, setNonce] = useState(0);
  const [previewToken, setPreviewToken] = useState(null);
  const [readiness, setReadiness] = useState(null);
  const [status, setStatus] = useState("idle"); // idle|preparing|ready|unavailable|expired|error

  const projectId = currentProjectId(preview.projectId);
  const path = preview.path || "index.html";

  const mintSession = useCallback(async () => {
    setStatus("preparing");
    setPreviewError(null);
    const sessionTok = getToken();
    if (!sessionTok) {
      setStatus("error");
      setPreviewError("Sign in required for workspace preview");
      return null;
    }
    try {
      const r = await fetch(`${baseUrl()}/api/files/${encodeURIComponent(projectId)}/preview-session`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${sessionTok}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ path }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        setStatus("unavailable");
        setPreviewError(err.detail || `Preview unavailable (${r.status})`);
        setPreviewToken(null);
        return null;
      }
      const data = await r.json();
      setPreviewToken(data.token);
      setReadiness(data.readiness || null);
      if (data.readiness?.readiness === "READY" || data.readiness?.readiness === "PENDING") {
        setStatus("ready");
      } else {
        setStatus("unavailable");
        setPreviewError(data.readiness?.detail || "Preview not ready");
      }
      return data;
    } catch (e) {
      setStatus("error");
      setPreviewError(String(e?.message || e));
      return null;
    }
  }, [projectId, path, setPreviewError]);

  useEffect(() => {
    if (!preview?.open || preview.minimized) return;
    mintSession();
  }, [preview?.open, preview?.minimized, projectId, path, nonce, mintSession]);

  const src = useMemo(() => {
    if (!previewToken) return null;
    const url = buildPreviewUrl(projectId, path, previewToken);
    return `${url}${url.includes("?") ? "&" : "?"}r=${nonce}`;
  }, [projectId, path, previewToken, nonce]);

  const onRefresh = useCallback(() => {
    setNonce((n) => n + 1);
  }, []);

  const onOpenBrowser = useCallback(async () => {
    let tok = previewToken;
    if (!tok) {
      const data = await mintSession();
      tok = data?.token;
    }
    if (!tok) return;
    const url = buildPreviewUrl(projectId, path, tok);
    const abs = `${window.location.origin}${url}`;
    const w = window.open(abs, "_blank", "noopener,noreferrer");
    if (!w) {
      try {
        if (navigator?.clipboard?.writeText) await navigator.clipboard.writeText(abs);
      } catch { /* ignore */ }
      setPreviewError("Popup blocked — preview URL copied if clipboard allowed.");
    }
  }, [previewToken, projectId, path, mintSession, setPreviewError]);

  if (!preview?.open) return null;

  if (preview.minimized) {
    return (
      <button type="button" className="sp-preview-chip" onClick={restorePreview} title="Restore preview">
        Preview · {path}
      </button>
    );
  }

  const statusLabel =
    status === "preparing" ? "Preparing preview…" :
    status === "ready" ? (readiness?.readiness === "PENDING" ? "Artifact pending…" : "Preview ready") :
    status === "unavailable" ? "Preview unavailable" :
    status === "expired" ? "Preview expired" :
    status === "error" ? "Preview error" : "";

  return (
    <div className="sp-preview-window" role="dialog" aria-label="Workspace preview">
      <div className="sp-preview-chrome">
        <div className="sp-preview-title">
          <span className="sp-preview-dot" />
          {preview.title || "Preview"}
          <span className="sp-preview-path">{path}</span>
          {statusLabel ? <span className="sp-preview-status">{statusLabel}</span> : null}
        </div>
        <div className="sp-preview-actions">
          <button type="button" className="sp-btn" onClick={onRefresh} title="Refresh">Refresh</button>
          <button type="button" className="sp-btn" onClick={onOpenBrowser} title="Open in Browser">Open in Browser</button>
          <button type="button" className="sp-btn" onClick={minimizePreview} title="Minimize">—</button>
          <button type="button" className="sp-btn" onClick={closePreview} title="Close">×</button>
        </div>
      </div>
      {preview.error ? <div className="sp-preview-error">{preview.error}</div> : null}
      <div className="sp-preview-frame-wrap">
        {src ? (
          <iframe
            key={src}
            title="workspace-preview"
            className="sp-preview-frame"
            src={src}
            sandbox={PREVIEW_IFRAME_SANDBOX}
            referrerPolicy="no-referrer"
            onError={() => setPreviewError("Failed to load preview")}
          />
        ) : (
          <div className="sp-preview-error">{statusLabel || "Waiting for preview authorization…"}</div>
        )}
      </div>
    </div>
  );
}
