/**
 * Workspace Preview — spatial surface for verified HTML/CSS/JS artifacts.
 * Presentation only: does not authorize UCIP or run missions.
 */
import React, { useCallback, useMemo, useState } from "react";
import useOsStore from "../store/osStore";
import { getToken } from "../../services/api";

function currentProjectId(explicit) {
  if (explicit) return explicit;
  try {
    return localStorage.getItem("devos_current_project") || "default";
  } catch {
    return "default";
  }
}

export function buildPreviewUrl(projectId, path, token) {
  const pid = encodeURIComponent(projectId || "default");
  const clean = (path || "index.html").replace(/^\/+/, "");
  const base = `/api/files/${pid}/preview/${clean.split("/").map(encodeURIComponent).join("/")}`;
  if (token) return `${base}?token=${encodeURIComponent(token)}`;
  return base;
}

export default function PreviewSurface() {
  const preview = useOsStore((s) => s.preview);
  const closePreview = useOsStore((s) => s.closePreview);
  const minimizePreview = useOsStore((s) => s.minimizePreview);
  const restorePreview = useOsStore((s) => s.restorePreview);
  const setPreviewError = useOsStore((s) => s.setPreviewError);

  const [nonce, setNonce] = useState(0);
  const token = useMemo(() => {
    try {
      return getToken();
    } catch {
      return null;
    }
  }, [preview.open, nonce]);

  const projectId = currentProjectId(preview.projectId);
  const path = preview.path || "index.html";
  const src = useMemo(() => {
    const url = buildPreviewUrl(projectId, path, token);
    return `${url}${url.includes("?") ? "&" : "?"}r=${nonce}`;
  }, [projectId, path, token, nonce]);

  const onRefresh = useCallback(() => {
    setPreviewError(null);
    setNonce((n) => n + 1);
  }, [setPreviewError]);

  const onOpenBrowser = useCallback(() => {
    const url = buildPreviewUrl(projectId, path, token);
    const abs = `${window.location.origin}${url}`;
    const w = window.open(abs, "_blank", "noopener,noreferrer");
    if (!w) {
      // Popup blocked — still expose URL for the user
      try {
        awaitNavigatorClipboard(abs);
      } catch {
        /* ignore */
      }
      setPreviewError("Popup blocked — preview URL copied if clipboard allowed. Open it from the address bar.");
    }
  }, [projectId, path, token, setPreviewError]);

  if (!preview?.open) return null;

  if (preview.minimized) {
    return (
      <button
        type="button"
        className="sp-preview-chip"
        onClick={restorePreview}
        title="Restore preview"
      >
        Preview · {path}
      </button>
    );
  }

  return (
    <div className="sp-preview-window" role="dialog" aria-label="Workspace preview">
      <div className="sp-preview-chrome">
        <div className="sp-preview-title">
          <span className="sp-preview-dot" />
          {preview.title || "Preview"}
          <span className="sp-preview-path">{path}</span>
        </div>
        <div className="sp-preview-actions">
          <button type="button" className="sp-btn" onClick={onRefresh} title="Refresh">
            Refresh
          </button>
          <button type="button" className="sp-btn" onClick={onOpenBrowser} title="Open in Browser">
            Open in Browser
          </button>
          <button type="button" className="sp-btn" onClick={minimizePreview} title="Minimize">
            —
          </button>
          <button type="button" className="sp-btn" onClick={closePreview} title="Close">
            ×
          </button>
        </div>
      </div>
      {preview.error ? (
        <div className="sp-preview-error">{preview.error}</div>
      ) : null}
      <div className="sp-preview-frame-wrap">
        <iframe
          key={src}
          title="workspace-preview"
          className="sp-preview-frame"
          src={src}
          sandbox="allow-scripts allow-same-origin allow-forms"
          referrerPolicy="no-referrer"
          onError={() => setPreviewError("Failed to load preview")}
        />
      </div>
    </div>
  );
}

function awaitNavigatorClipboard(text) {
  if (navigator?.clipboard?.writeText) {
    navigator.clipboard.writeText(text);
  }
}
