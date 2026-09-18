/**
 * Preview — first-class Spatial OS dimension.
 * In-app iframe is authoritative; external browser is optional.
 * Presentation only: does not authorize UCIP or run missions.
 *
 * Sandbox: allow-scripts only (opaque origin — no same-origin DevOS access).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Play, Square, RotateCcw, RefreshCw, ExternalLink, Copy, Maximize2, Minimize2,
  X, Monitor, Smartphone, Tablet, ChevronDown, Terminal, Settings, MoreHorizontal,
} from "lucide-react";
import useOsStore from "../store/osStore";
import { getToken, baseUrl, api } from "../../services/api";
import { resolvePreviewChrome } from "../preview/previewLayout";

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

export const PREVIEW_IFRAME_SANDBOX = "allow-scripts";

const DEVICES = {
  fluid: { label: "Fluid", width: null },
  desktop: { label: "Desktop", width: 1280 },
  tablet: { label: "Tablet", width: 768 },
  mobile: { label: "Mobile", width: 390 },
};

export default function PreviewSurface({ embedded = false }) {
  const preview = useOsStore((s) => s.preview);
  const closePreview = useOsStore((s) => s.closePreview);
  const minimizePreview = useOsStore((s) => s.minimizePreview);
  const restorePreview = useOsStore((s) => s.restorePreview);
  const setPreviewError = useOsStore((s) => s.setPreviewError);
  const setActiveWorkspace = useOsStore((s) => s.setActiveWorkspace);
  const spatialFullscreenId = useOsStore((s) => s.spatialFullscreenId);
  const setSpatialFullscreenId = useOsStore((s) => s.setSpatialFullscreenId);
  const previewUi = useOsStore((s) => s.previewUi) || {};
  const setPreviewUi = useOsStore((s) => s.setPreviewUi);

  const [nonce, setNonce] = useState(0);
  const [previewToken, setPreviewToken] = useState(null);
  const [readiness, setReadiness] = useState(null);
  const [status, setStatus] = useState("idle");
  const [notice, setNotice] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [consoleLines, setConsoleLines] = useState([]);
  const [runtimeSnap, setRuntimeSnap] = useState(null);
  const [runtimeBusy, setRuntimeBusy] = useState(false);
  const rootRef = useRef(null);
  const [size, setSize] = useState({ width: 800, height: 600 });

  const projectId = currentProjectId(preview.projectId);
  const path = preview.path || "index.html";
  const device = previewUi.device || "fluid";
  const zoom = Number(previewUi.zoom) || 100;
  const consoleOpen = !!previewUi.consoleOpen;

  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (cr) setSize({ width: cr.width, height: cr.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const chrome = resolvePreviewChrome(size);

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
      const r = await fetch(
        `${baseUrl()}/api/files/${encodeURIComponent(projectId)}/preview-session`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${sessionTok}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ path }),
        }
      );
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        setStatus("error");
        setPreviewError(data.detail || data.message || `Preview session failed (${r.status})`);
        return null;
      }
      setPreviewToken(data.token || data.preview_token || null);
      setReadiness(data.readiness || null);
      if (data.readiness?.readiness === "UNAVAILABLE" || data.ok === false) {
        setStatus("unavailable");
        setPreviewError(data.readiness?.detail || "Preview not ready");
      } else {
        setStatus("ready");
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

  const absoluteUrl = useMemo(() => {
    if (!previewToken) return null;
    const url = buildPreviewUrl(projectId, path, previewToken);
    return `${window.location.origin}${url}`;
  }, [projectId, path, previewToken]);

  const onRefresh = useCallback(() => {
    setNonce((n) => n + 1);
    setNotice(null);
  }, []);

  const refreshRuntime = useCallback(async () => {
    try {
      const snap = await api.runtimeStatus(projectId);
      setRuntimeSnap(snap);
      return snap;
    } catch (e) {
      setConsoleLines((lines) =>
        [...lines, `[runtime] status failed: ${e?.message || e}`].slice(-80)
      );
      return null;
    }
  }, [projectId]);

  useEffect(() => {
    if (!preview?.open || preview.minimized) return undefined;
    refreshRuntime();
    const id = setInterval(() => {
      refreshRuntime();
    }, 12000);
    return () => clearInterval(id);
  }, [preview?.open, preview?.minimized, projectId, refreshRuntime]);

  const runtimeAction = useCallback(
    async (action) => {
      setRuntimeBusy(true);
      try {
        const snap = await api.runtimeAction(projectId, action);
        setRuntimeSnap(snap);
        setConsoleLines((lines) =>
          [
            ...lines,
            `[runtime] ${action} → ${snap?.state || "?"} health=${snap?.health || "?"}`,
          ].slice(-80)
        );
        if (action === "start" || action === "restart") {
          setNonce((n) => n + 1);
        }
      } catch (e) {
        setPreviewError(String(e?.message || e));
        setConsoleLines((lines) =>
          [...lines, `[runtime] ${action} error: ${e?.message || e}`].slice(-80)
        );
      } finally {
        setRuntimeBusy(false);
      }
    },
    [projectId, setPreviewError]
  );

  const copyUrl = useCallback(async () => {
    let abs = absoluteUrl;
    if (!abs) {
      const data = await mintSession();
      const tok = data?.token || data?.preview_token;
      if (tok) {
        abs = `${window.location.origin}${buildPreviewUrl(projectId, path, tok)}`;
      }
    }
    if (!abs) {
      setNotice({ type: "error", text: "No preview URL available yet." });
      return;
    }
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(abs);
        setNotice({ type: "info", text: "Preview URL copied to clipboard." });
      } else {
        setNotice({ type: "info", text: abs });
      }
    } catch {
      setNotice({ type: "error", text: "Could not copy URL." });
    }
  }, [absoluteUrl, mintSession, projectId, path]);

  const onOpenBrowser = useCallback(async () => {
    let tok = previewToken;
    if (!tok) {
      const data = await mintSession();
      tok = data?.token || data?.preview_token;
    }
    if (!tok) return;
    const abs = `${window.location.origin}${buildPreviewUrl(projectId, path, tok)}`;
    let win = null;
    try {
      win = window.open(abs, "_blank", "noopener,noreferrer");
    } catch {
      win = null;
    }
    if (!win) {
      try {
        if (navigator?.clipboard?.writeText) await navigator.clipboard.writeText(abs);
      } catch {
        /* ignore */
      }
      // Accurate: popup blocked — not "ad blocker"
      setNotice({
        type: "warn",
        text:
          "The browser blocked a popup window. In-app Preview is still available. Preview URL was copied — paste it in a new tab if you need an external window.",
      });
      setPreviewError(null);
    } else {
      setNotice({ type: "info", text: "Opened in a new browser tab." });
    }
  }, [previewToken, projectId, path, mintSession, setPreviewError]);

  const toggleFullscreen = () => {
    if (spatialFullscreenId === "preview") {
      setSpatialFullscreenId(null);
    } else {
      setActiveWorkspace?.("preview");
      setSpatialFullscreenId("preview");
    }
  };

  if (!preview?.open) return null;

  // Minimized: floating chip only from the non-embedded host (shell)
  if (preview.minimized) {
    if (embedded) return null;
    return (
      <button
        type="button"
        className="sp-preview-chip"
        onClick={() => {
          restorePreview();
          setActiveWorkspace?.("preview");
        }}
        title="Restore preview dimension"
      >
        Preview · {path}
      </button>
    );
  }

  // Full preview lives in SpatialWorkspace (embedded). Shell host skips duplicate.
  if (!embedded) return null;

  const statusLabel =
    status === "preparing"
      ? "Preparing…"
      : status === "ready"
        ? readiness?.readiness === "PENDING"
          ? "Pending"
          : "Ready"
        : status === "unavailable"
          ? "Unavailable"
          : status === "expired"
            ? "Expired"
            : status === "error"
              ? "Error"
              : "";

  const deviceW = DEVICES[device]?.width;
  const frameStyle = {
    width: deviceW ? Math.min(deviceW, size.width - 16) : "100%",
    maxWidth: "100%",
    transform: zoom !== 100 ? `scale(${zoom / 100})` : undefined,
    transformOrigin: "top center",
    height:
      zoom !== 100
        ? `${Math.round(10000 / zoom)}%`
        : "100%",
  };

  const rtState = runtimeSnap?.state || "—";
  const rtHealth = runtimeSnap?.health || "unknown";
  const rtKind = runtimeSnap?.detection?.kind || runtimeSnap?.components?.[0]?.name || "";

  const primary = (
    <>
      <span
        className={"sp-pv-rt-badge sp-pv-rt-badge--" + String(rtHealth).replace(/[^a-z]/gi, "")}
        title={(runtimeSnap?.detail || "") + (rtKind ? ` · ${rtKind}` : "")}
      >
        {runtimeBusy ? "…" : rtState}
        {rtHealth && rtHealth !== "unknown" ? ` · ${rtHealth}` : ""}
      </span>
      <button
        type="button"
        className="sp-pv-btn"
        disabled={runtimeBusy}
        onClick={() => runtimeAction("start")}
        title="Start runtime"
      >
        <Play size={14} />
        {chrome.showLabels && <span>Start</span>}
      </button>
      <button
        type="button"
        className="sp-pv-btn"
        disabled={runtimeBusy}
        onClick={() => runtimeAction("stop")}
        title="Stop runtime"
      >
        <Square size={14} />
        {chrome.showLabels && <span>Stop</span>}
      </button>
      <button
        type="button"
        className="sp-pv-btn"
        disabled={runtimeBusy}
        onClick={() => runtimeAction("restart")}
        title="Restart runtime"
      >
        <RotateCcw size={14} />
        {chrome.showLabels && <span>Restart</span>}
      </button>
      <button
        type="button"
        className="sp-pv-btn"
        disabled={runtimeBusy}
        onClick={() => runtimeAction("rebuild")}
        title="Rebuild (install + build)"
      >
        {chrome.showLabels ? <span>Rebuild</span> : <span>Rb</span>}
      </button>
      <button type="button" className="sp-pv-btn" onClick={onRefresh} title="Refresh preview">
        <RefreshCw size={14} />
        {chrome.showLabels && <span>Refresh</span>}
      </button>
    </>
  );

  const secondaryMenu = (
    <div className="sp-pv-menu" role="menu">
      <button type="button" role="menuitem" onClick={() => { setPreviewUi({ device: "fluid" }); setMenuOpen(false); }}>
        <Monitor size={14} /> Fluid viewport
      </button>
      <button type="button" role="menuitem" onClick={() => { setPreviewUi({ device: "desktop" }); setMenuOpen(false); }}>
        <Monitor size={14} /> Desktop 1280
      </button>
      <button type="button" role="menuitem" onClick={() => { setPreviewUi({ device: "tablet" }); setMenuOpen(false); }}>
        <Tablet size={14} /> Tablet 768
      </button>
      <button type="button" role="menuitem" onClick={() => { setPreviewUi({ device: "mobile" }); setMenuOpen(false); }}>
        <Smartphone size={14} /> Mobile 390
      </button>
      <hr />
      <button type="button" role="menuitem" onClick={() => { setPreviewUi({ zoom: Math.max(50, zoom - 10) }); }}>
        Zoom −
      </button>
      <button type="button" role="menuitem" onClick={() => { setPreviewUi({ zoom: Math.min(150, zoom + 10) }); }}>
        Zoom +
      </button>
      <button type="button" role="menuitem" onClick={() => { setPreviewUi({ zoom: 100 }); }}>
        Zoom 100%
      </button>
      <hr />
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          runtimeAction("health");
          setMenuOpen(false);
        }}
      >
        Health check
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          runtimeAction("test");
          setMenuOpen(false);
        }}
      >
        Run tests
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          setPreviewUi({ consoleOpen: !consoleOpen });
          setMenuOpen(false);
        }}
      >
        <Terminal size={14} /> {consoleOpen ? "Hide console" : "Show console"}
      </button>
      <button type="button" role="menuitem" onClick={() => { copyUrl(); setMenuOpen(false); }}>
        <Copy size={14} /> Copy URL
      </button>
      <button type="button" role="menuitem" onClick={() => { onOpenBrowser(); setMenuOpen(false); }}>
        <ExternalLink size={14} /> Open external browser
      </button>
      <button type="button" role="menuitem" onClick={() => { toggleFullscreen(); setMenuOpen(false); }}>
        <Maximize2 size={14} /> {spatialFullscreenId === "preview" ? "Exit fullscreen" : "Fullscreen"}
      </button>
    </div>
  );

  return (
    <div
      className={`sp-preview-dimension ${embedded ? "sp-preview-dimension--embedded" : ""}`}
      role="region"
      aria-label="Workspace preview"
      ref={rootRef}
      data-preview-chrome={chrome.density}
    >
      <div className="sp-preview-toolbar">
        <div className="sp-preview-title">
          <span className="sp-preview-dot" />
          <span className="sp-preview-name">{preview.title || "Preview"}</span>
          <span className="sp-preview-path" title={path}>
            {path}
          </span>
          {statusLabel ? <span className="sp-preview-status">{statusLabel}</span> : null}
        </div>

        <div className="sp-preview-actions">
          {primary}

          {chrome.showDeviceInline && (
            <select
              className="sp-pv-select"
              value={device}
              onChange={(e) => setPreviewUi({ device: e.target.value })}
              title="Viewport"
              aria-label="Viewport device"
            >
              {Object.entries(DEVICES).map(([k, v]) => (
                <option key={k} value={k}>
                  {v.label}
                </option>
              ))}
            </select>
          )}

          {chrome.showZoomInline && (
            <select
              className="sp-pv-select"
              value={String(zoom)}
              onChange={(e) => setPreviewUi({ zoom: Number(e.target.value) })}
              title="Zoom"
              aria-label="Zoom"
            >
              {[50, 75, 100, 125, 150].map((z) => (
                <option key={z} value={z}>
                  {z}%
                </option>
              ))}
            </select>
          )}

          {chrome.showSecondaryInline && (
            <>
              <button
                type="button"
                className="sp-pv-btn"
                onClick={() => setPreviewUi({ consoleOpen: !consoleOpen })}
                title="Console"
              >
                <Terminal size={14} />
                {chrome.showLabels && <span>Console</span>}
              </button>
              <button type="button" className="sp-pv-btn" onClick={copyUrl} title="Copy URL">
                <Copy size={14} />
                {chrome.showLabels && <span>Copy URL</span>}
              </button>
              <button type="button" className="sp-pv-btn" onClick={onOpenBrowser} title="External browser">
                <ExternalLink size={14} />
                {chrome.showLabels && <span>External</span>}
              </button>
              <button
                type="button"
                className="sp-pv-btn"
                onClick={toggleFullscreen}
                title="Fullscreen"
              >
                {spatialFullscreenId === "preview" ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
              </button>
            </>
          )}

          {chrome.useOverflowMenu && (
            <div className="sp-pv-menu-wrap">
              <button
                type="button"
                className="sp-pv-btn"
                aria-expanded={menuOpen}
                aria-haspopup="menu"
                onClick={() => setMenuOpen((v) => !v)}
                title="More preview controls"
              >
                <MoreHorizontal size={14} />
                {chrome.showLabels && <span>More</span>}
                <ChevronDown size={12} />
              </button>
              {menuOpen ? secondaryMenu : null}
            </div>
          )}

          <button type="button" className="sp-pv-btn" onClick={minimizePreview} title="Minimize">
            <Minimize2 size={14} />
          </button>
          <button type="button" className="sp-pv-btn" onClick={closePreview} title="Close">
            <X size={14} />
          </button>
        </div>
      </div>

      {(notice || preview.error) && (
        <div
          className={`sp-preview-notice sp-preview-notice--${notice?.type || "error"}`}
          role="status"
        >
          <span>{notice?.text || preview.error}</span>
          <button type="button" className="sp-pv-btn" onClick={() => { setNotice(null); setPreviewError(null); }}>
            <X size={12} />
          </button>
        </div>
      )}

      <div className="sp-preview-stage">
        <div className="sp-preview-frame-wrap" data-device={device}>
          {src ? (
            <iframe
              key={src}
              title="workspace-preview"
              className="sp-preview-frame"
              src={src}
              sandbox={PREVIEW_IFRAME_SANDBOX}
              referrerPolicy="no-referrer"
              style={frameStyle}
              onError={() => setPreviewError("Failed to load preview")}
            />
          ) : (
            <div className="sp-preview-placeholder">
              {statusLabel || "Waiting for preview authorization…"}
            </div>
          )}
        </div>

        {consoleOpen && (
          <div className="sp-preview-console" aria-label="Preview console">
            <div className="sp-preview-console-head">
              <span>Console</span>
              <button type="button" className="sp-pv-btn" onClick={() => setPreviewUi({ consoleOpen: false })}>
                <X size={12} />
              </button>
            </div>
            <pre className="sp-preview-console-body">
              {consoleLines.length
                ? consoleLines.join("\n")
                : "Runtime messages appear here. In-app Preview does not require an external window."}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
