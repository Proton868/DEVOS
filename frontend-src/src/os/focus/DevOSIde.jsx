/**
 * DevOS IDE — first-class spatial dimension with internal organization.
 * Editor remains primary; explorer/search/scm/bottom panels dock or sheet
 * based on available IDE area. Monaco + save paths unchanged.
 */
import React, { useEffect, useState, useCallback, useRef, Suspense, lazy } from "react";
import Editor from "@monaco-editor/react";
import {
  ChevronLeft, Save, X, FileCode2, RefreshCw, PanelLeft, Eye, Minimize2,
  Files, Search, GitBranch, AlertCircle, Play, Terminal as TerminalIcon,
  PanelBottom,
} from "lucide-react";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api, getLanguageFromPath } from "../../services/api";
import { loadMonaco, probeMonacoAssets } from "../../monacoSetup";
import { resolveIdeLayout } from "../ide/ideLayout";
import IdeTabBar from "../ide/IdeTabBar";
import { flattenDiagnostics } from "../ide/ideTabs";
import { listIdeCommands } from "../ide/ideCommands";
import { useLSP } from "../../hooks/useLSP";

const GitPanel = lazy(() => import("../../components/sidebar/GitPanel"));
const SearchPanel = lazy(() => import("../../components/sidebar/SearchPanel"));

const LOAD_TIMEOUT_MS = 45000;

const LANG_COLORS = {
  JavaScript: "#f1e05a",
  TypeScript: "#3178c6",
  Python: "#3572A5",
  HTML: "#e34c26",
  CSS: "#563d7c",
  SCSS: "#c6538c",
  JSON: "#292929",
  Markdown: "#083fa1",
  Shell: "#89e051",
  YAML: "#cb171e",
  Other: "#8b949e",
};

function langFromName(name) {
  const n = String(name || "").toLowerCase();
  if (n.endsWith(".js") || n.endsWith(".jsx") || n.endsWith(".mjs") || n.endsWith(".cjs")) return "JavaScript";
  if (n.endsWith(".ts") || n.endsWith(".tsx")) return "TypeScript";
  if (n.endsWith(".py")) return "Python";
  if (n.endsWith(".html") || n.endsWith(".htm")) return "HTML";
  if (n.endsWith(".css")) return "CSS";
  if (n.endsWith(".scss") || n.endsWith(".sass")) return "SCSS";
  if (n.endsWith(".json")) return "JSON";
  if (n.endsWith(".md") || n.endsWith(".mdx")) return "Markdown";
  if (n.endsWith(".sh") || n.endsWith(".bash")) return "Shell";
  if (n.endsWith(".yml") || n.endsWith(".yaml")) return "YAML";
  return "Other";
}

function languageBreakdown(fileList) {
  const counts = {};
  let total = 0;
  for (const f of fileList || []) {
    const name = f.name || f.path || f;
    if (f.type === "dir" || f.is_dir) continue;
    const lang = langFromName(name);
    counts[lang] = (counts[lang] || 0) + 1;
    total += 1;
  }
  if (!total) return [];
  return Object.entries(counts)
    .map(([lang, n]) => ({ lang, n, pct: (n / total) * 100, color: LANG_COLORS[lang] || LANG_COLORS.Other }))
    .sort((a, b) => b.n - a.n);
}

function SideFallback() {
  return <div className="sp-ide-side-fallback">Loading…</div>;
}

function ProblemsPanel({ error, statusText }) {
  const items = [];
  if (error) items.push({ severity: "error", message: error });
  if (!items.length) {
    return (
      <div className="sp-ide-panel-empty">
        No problems detected. Diagnostics from builds/tests will appear here.
      </div>
    );
  }
  return (
    <ul className="sp-ide-problems">
      {items.map((it, i) => (
        <li key={i} className={`sp-ide-problem sp-ide-problem--${it.severity}`}>
          <AlertCircle size={12} />
          <span>{it.message}</span>
        </li>
      ))}
    </ul>
  );
}

function OutputPanel({ statusText }) {
  return (
    <pre className="sp-ide-output">{statusText || "No output yet."}</pre>
  );
}

function RunPanel({ onOpenTerminal }) {
  return (
    <div className="sp-ide-panel-empty">
      <p>Run / Debug uses governed execution from Nuha and the Terminal dimension.</p>
      <button type="button" className="sp-ide-linkbtn" onClick={onOpenTerminal}>
        Open Terminal
      </button>
    </div>
  );
}

export default function DevOSIde({ onClose, onCollapse }) {
  const editor = useOsStore((s) => s.editor);
  const closeEditor = useOsStore((s) => s.closeEditor);
  const openEditor = useOsStore((s) => s.openEditor);
  const openPreview = useOsStore((s) => s.openPreview);
  const openTerminal = useOsStore((s) => s.openTerminal);
  const ideLayout = useOsStore((s) => s.ideLayout) || {};
  const ideWorkspace = useOsStore((s) => s.ideWorkspace) || {};
  const markIdeTabModified = useOsStore((s) => s.markIdeTabModified);
  const setIdeLayout = useOsStore((s) => s.setIdeLayout);
  const toggleIdeActivity = useOsStore((s) => s.toggleIdeActivity);
  const toggleIdeBottom = useOsStore((s) => s.toggleIdeBottom);

  const setStatus = useStore((s) => s.setStatus);
  const statusText = useStore((s) => s.status);

  const [content, setContent] = useState(null);
  const [original, setOriginal] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [monacoReady, setMonacoReady] = useState(false);
  const [monacoError, setMonacoError] = useState(null);
  const [monacoAttempt, setMonacoAttempt] = useState(0);
  const [files, setFiles] = useState([]);
  const [ideSize, setIdeSize] = useState({ width: 900, height: 640 });
  const editorRef = useRef(null);
  const monacoRef = useRef(null);
  const rootRef = useRef(null);
  const loadGen = useRef(0);

  const target = editor.file
    ? { type: "file", path: editor.file }
    : editor.scriptId != null
    ? { type: "script", id: editor.scriptId }
    : null;

  const language =
    editor.language ||
    (editor.file ? getLanguageFromPath(editor.file) : "python");

  const tabPath = editor.file || (target?.type === "file" ? target.path : null);
  const { notifyOpen, notifyChange } = useLSP(
    editorRef,
    monacoRef,
    tabPath,
    language
  );
  const mergeIdeDiagnostics = useOsStore((s) => s.mergeIdeDiagnostics);

  // Measure IDE surface for internal spatial resolve
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (cr) setIdeSize({ width: cr.width, height: cr.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const plan = resolveIdeLayout(ideSize, ideLayout);

  const loadContent = useCallback(async () => {
    if (!target) return;
    const gen = ++loadGen.current;
    setLoading(true);
    setError(null);
    setContent(null);

    const timeout = new Promise((_, reject) =>
      setTimeout(() => reject(new Error("Load timed out — check network or project path")), LOAD_TIMEOUT_MS)
    );

    try {
      const work = (async () => {
        let code = "";
        let lang = language;
        if (target.type === "file") {
          const r = await api.readFile(target.path);
          code = r?.content ?? r?.data ?? (typeof r === "string" ? r : "");
          if (code == null) code = "";
          lang = getLanguageFromPath(target.path);
        } else {
          const s = await api.flowScript(target.id);
          code = s?.code ?? s?.content ?? "";
          if (s?.language) {
            lang = s.language.toLowerCase() === "python" ? "python" : s.language.toLowerCase();
          }
        }
        return { code: String(code), lang };
      })();

      const { code, lang } = await Promise.race([work, timeout]);
      if (gen !== loadGen.current) return;
      setContent(code);
      setOriginal(code);
      useOsStore.setState((st) => ({ editor: { ...st.editor, language: lang } }));
    } catch (e) {
      if (gen !== loadGen.current) return;
      setError(e?.message || String(e));
      setContent("");
      setOriginal("");
    } finally {
      if (gen === loadGen.current) setLoading(false);
    }
  }, [target?.type, target?.path, target?.id, language]);

  useEffect(() => {
    loadContent();
  }, [loadContent]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.listDir("");
        const items = Array.isArray(r) ? r : (r?.entries || r?.items || []);
        if (!cancelled) setFiles(items);
      } catch {
        if (!cancelled) setFiles([]);
      }
    })();
    return () => { cancelled = true; };
  }, [target?.path]);

  useEffect(() => {
    if (loading || error) return;
    let cancelled = false;
    setMonacoError(null);
    setMonacoReady(false);
    (async () => {
      try {
        const probe = await probeMonacoAssets();
        if (cancelled) return;
        if (!probe.ok) {
          setMonacoError(
            `Monaco assets unavailable (${probe.reason}). HTTP ${probe.status}.`
          );
          return;
        }
        await loadMonaco();
        if (cancelled) return;
        setMonacoReady(true);
        setMonacoError(null);
      } catch (e) {
        if (cancelled) return;
        const msg = e?.message || String(e);
        console.error("[DevOSIde] Monaco init failed:", msg, e);
        setMonacoError(msg);
        setMonacoReady(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loading, error, monacoAttempt, target?.path, target?.id]);

  const dirty = content !== null && content !== original;

  const save = useCallback(async () => {
    if (!target || content === null) return;
    setSaving(true);
    setStatus("Saving…");
    try {
      if (target.type === "file") {
        await api.writeFile(target.path, content);
      } else {
        await api.updateFlowScript(target.id, { code: content });
      }
      setOriginal(content);
      if (target?.type === "file" && target.path) {
        try { markIdeTabModified?.(target.path, false); } catch (_) {}
      }
      setStatus("Saved ✓");
      setTimeout(() => useStore.getState().setStatus("Ready"), 1500);
    } catch (e) {
      setStatus("Save failed: " + e.message);
    } finally {
      setSaving(false);
    }
  }, [target, content, setStatus, markIdeTabModified]);

  useEffect(() => {
    const h = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [save]);


  const formatDocument = useCallback(async () => {
    const ed = editorRef.current;
    if (!ed) return;
    try {
      const action = ed.getAction?.("editor.action.formatDocument");
      if (action) {
        await action.run();
        setStatus("Formatted");
      } else {
        setStatus("Format action unavailable");
      }
    } catch (e) {
      setStatus("Format failed: " + (e.message || e));
    }
  }, [setStatus]);

  const gotoSymbol = useCallback(async () => {
    const ed = editorRef.current;
    if (!ed) return;
    try {
      await ed.getAction?.("editor.action.quickOutline")?.run?.();
    } catch (_) {
      setStatus("Symbol navigation unavailable");
    }
  }, [setStatus]);

  useEffect(() => {
    const h = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "f") {
        // avoid clash with OS search; use Alt+Shift+F for format
      }
      if (e.altKey && e.shiftKey && e.key.toLowerCase() === "f") {
        e.preventDefault();
        formatDocument();
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "o" && e.shiftKey) {
        e.preventDefault();
        gotoSymbol();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [formatDocument, gotoSymbol]);

  useEffect(() => {
    const h = (ev) => {
      const id = ev?.detail?.id;
      if (id === "ide.editor.format") formatDocument();
      if (id === "ide.goto.symbol") gotoSymbol();
    };
    window.addEventListener("devos-ide-command", h);
    return () => window.removeEventListener("devos-ide-command", h);
  }, [formatDocument, gotoSymbol]);


  const onSidebarResize = (e) => {
    if (plan.sidebarMode !== "docked") return;
    e.preventDefault();
    const startX = e.clientX;
    const startW = plan.sidebarWidth;
    const onMove = (ev) => {
      const dx = ev.clientX - startX;
      setIdeLayout({ sidebarWidth: Math.min(420, Math.max(180, startW + dx)) });
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const onBottomResize = (e) => {
    if (plan.bottomMode !== "docked") return;
    e.preventDefault();
    const startY = e.clientY;
    const startH = plan.bottomHeight;
    const onMove = (ev) => {
      const dy = startY - ev.clientY;
      setIdeLayout({ bottomHeight: Math.min(360, Math.max(100, startH + dy)) });
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const openTerminalPanel = () => {
    toggleIdeBottom("terminal");
    openTerminal?.({});
  };

  if (!target) {
    // Empty IDE dimension — still show chrome so it feels like a workspace
    return (
      <div className="sp-surface sp-surface--seamless sp-ide-root" ref={rootRef}>
        <div className="sp-surface-head">
          <button className="sp-iconbtn" title="Collapse IDE" onClick={() => onCollapse && onCollapse()}>
            <Minimize2 size={15} />
          </button>
          <span>DEVOS IDE</span>
          <span className="sub">· no file open</span>
          <span className="spacer" />
          <button className="sp-iconbtn" title="Close" onClick={() => { closeEditor(); onClose && onClose(); }}>
            <X size={15} />
          </button>
        </div>
        <div className="sp-ide-empty-dim">
          Open a file from Explorer or Nuha to begin editing.
        </div>
      </div>
    );
  }

  const title = target.type === "file" ? target.path : `script #${target.id}`;
  const breakdown = languageBreakdown(files);

  const activity = ideLayout.activity || "explorer";
  const sideVisible = plan.sidebarMode === "docked" || plan.sidebarMode === "sheet";
  const bottomVisible = plan.bottomMode === "docked" || plan.bottomMode === "sheet";

  const renderSideBody = () => {
    if (activity === "search") {
      return (
        <Suspense fallback={<SideFallback />}>
          <SearchPanel embedded onClose={() => setIdeLayout({ sidebarOpen: false })} />
        </Suspense>
      );
    }
    if (activity === "scm") {
      return (
        <Suspense fallback={<SideFallback />}>
          <GitPanel />
        </Suspense>
      );
    }
    if (activity === "problems") {
      return <ProblemsPanel error={error || monacoError} statusText={statusText} />;
    }
    if (activity === "run") {
      return <RunPanel onOpenTerminal={openTerminalPanel} />;
    }
    // explorer — open files through osStore.openEditor (IDE dimension state)
    return (
      <div className="sp-ide-filelist">
        {(files || []).length === 0 && (
          <div className="sp-ide-panel-empty">No files in project root.</div>
        )}
        {(files || []).map((f) => {
          const path = f.path || f.name || f;
          const name = String(path).split("/").pop();
          const isDir = f.type === "dir" || f.is_dir;
          return (
            <button
              key={path}
              type="button"
              className={"sp-ide-file" + (target?.path === path ? " active" : "")}
              onClick={() => {
                if (!isDir) openEditor({ file: path });
              }}
              title={path}
            >
              {isDir ? "📁 " : ""}
              {name}
            </button>
          );
        })}
      </div>
    );
  };

  return (
    <div
      className="sp-surface sp-surface--seamless sp-ide-root"
      ref={rootRef}
      data-ide-sidebar={plan.sidebarMode}
      data-ide-bottom={plan.bottomMode}
    >
      <div className="sp-surface-head">
        <button
          className="sp-iconbtn"
          title="Collapse IDE dimension"
          onClick={() => onCollapse && onCollapse()}
        >
          <Minimize2 size={15} />
        </button>
        <button
          className="sp-iconbtn"
          title="Back to canvas"
          onClick={() => {
            closeEditor();
            onClose && onClose();
          }}
        >
          <ChevronLeft size={15} />
        </button>
        <span>DEVOS IDE</span>
        <span className="sub">· {title}</span>
        <span className="spacer" />
        {target.type === "file" && String(title).match(/\.(html?|css|js)$/i) && (
          <button
            className="sp-iconbtn"
            title="Preview"
            onClick={() => openPreview?.({ path: title })}
          >
            <Eye size={15} />
          </button>
        )}
        <button
          className="sp-iconbtn"
          title="Reload"
          onClick={() => {
            setMonacoAttempt((n) => n + 1);
            loadContent();
          }}
        >
          <RefreshCw size={15} />
        </button>
        <button
          className="sp-iconbtn"
          title="Save"
          disabled={saving || !dirty}
          onClick={save}
        >
          <Save size={15} />
        </button>
        <button
          className="sp-iconbtn"
          title="Close"
          onClick={() => {
            closeEditor();
            onClose && onClose();
          }}
        >
          <X size={15} />
        </button>
      </div>

      <div className="sp-ide-main">
        {/* Activity bar */}
        {plan.showActivityBar && (
          <div className="sp-ide-activity" role="toolbar" aria-label="IDE activity">
            <button
              type="button"
              className={"sp-ide-act" + (activity === "explorer" && ideLayout.sidebarOpen ? " active" : "")}
              title="Explorer"
              onClick={() => toggleIdeActivity("explorer")}
            >
              <Files size={16} />
            </button>
            <button
              type="button"
              className={"sp-ide-act" + (activity === "search" && ideLayout.sidebarOpen ? " active" : "")}
              title="Search"
              onClick={() => toggleIdeActivity("search")}
            >
              <Search size={16} />
            </button>
            <button
              type="button"
              className={"sp-ide-act" + (activity === "scm" && ideLayout.sidebarOpen ? " active" : "")}
              title="Source Control"
              onClick={() => toggleIdeActivity("scm")}
            >
              <GitBranch size={16} />
            </button>
            <button
              type="button"
              className={"sp-ide-act" + (activity === "problems" && ideLayout.sidebarOpen ? " active" : "")}
              title="Problems"
              onClick={() => toggleIdeActivity("problems")}
            >
              <AlertCircle size={16} />
            </button>
            <button
              type="button"
              className={"sp-ide-act" + (activity === "run" && ideLayout.sidebarOpen ? " active" : "")}
              title="Run / Debug"
              onClick={() => toggleIdeActivity("run")}
            >
              <Play size={16} />
            </button>
            <span className="sp-ide-act-spacer" />
            <button
              type="button"
              className={"sp-ide-act" + (ideLayout.bottomOpen && ideLayout.bottom === "terminal" ? " active" : "")}
              title="Terminal"
              onClick={openTerminalPanel}
            >
              <TerminalIcon size={16} />
            </button>
            <button
              type="button"
              className={"sp-ide-act" + (ideLayout.bottomOpen && ideLayout.bottom === "output" ? " active" : "")}
              title="Output"
              onClick={() => toggleIdeBottom("output")}
            >
              <PanelBottom size={16} />
            </button>
          </div>
        )}

        {/* Side panel */}
        {sideVisible && (
          <div
            className={`sp-ide-side sp-ide-side--${plan.sidebarMode}`}
            style={
              plan.sidebarMode === "docked"
                ? { width: plan.sidebarWidth }
                : undefined
            }
          >
            <div className="sp-ide-side-head">
              <span>{activity === "scm" ? "Source Control" : activity === "search" ? "Search" : activity === "problems" ? "Problems" : activity === "run" ? "Run" : "Explorer"}</span>
              <button
                type="button"
                className="sp-iconbtn"
                title="Close panel"
                onClick={() => setIdeLayout({ sidebarOpen: false })}
              >
                <X size={13} />
              </button>
            </div>
            <div className="sp-ide-side-body sp-ide-files">{renderSideBody()}</div>
          </div>
        )}
        {plan.sidebarMode === "docked" && sideVisible && (
          <div
            className="sp-ide-side-resizer"
            onMouseDown={onSidebarResize}
            role="separator"
            aria-orientation="vertical"
          />
        )}

        {/* Editor column (always primary) */}
        <div className="sp-ide-editor-col">
          <IdeTabBar />
          <div className="sp-ide-editor-toolbar">
            <button type="button" className="sp-pv-btn" onClick={formatDocument} title="Format Document (Alt+Shift+F)">
              Format
            </button>
            <button type="button" className="sp-pv-btn" onClick={gotoSymbol} title="Go to Symbol">
              Symbols
            </button>
            <span className="sp-ide-editor-toolbar-hint">{listIdeCommands().length} commands</span>
          </div>
          {breakdown.length > 0 && (
            <div className="sp-ide-langbar" title="Project language mix">
              {breakdown.map((b) => (
                <span
                  key={b.lang}
                  style={{
                    width: `${Math.max(4, b.pct)}%`,
                    background: b.color,
                  }}
                  title={`${b.lang}: ${b.n}`}
                />
              ))}
            </div>
          )}

          {loading && (
            <div className="sp-ide-center-msg">Loading file…</div>
          )}
          {error && !loading && (
            <div className="sp-ide-center-msg sp-ide-center-msg--err">
              {error}
              <button type="button" className="sp-ide-linkbtn" onClick={loadContent}>
                Retry
              </button>
            </div>
          )}
          {monacoError && !loading && (
            <div className="sp-ide-center-msg sp-ide-center-msg--err">
              Editor failed to initialize: {monacoError}
              <button
                type="button"
                className="sp-ide-linkbtn"
                onClick={() => setMonacoAttempt((n) => n + 1)}
              >
                Retry
              </button>
            </div>
          )}

          {!loading && !error && !monacoError && content !== null && (
            <div className="sp-ide-body">
              <Editor
                key={`${title}:${monacoAttempt}`}
                language={language}
                value={content || ""}
                theme="vs-dark"
                loading={
                  <span style={{ color: "var(--sp-text-2)", fontSize: 12 }}>
                    Mounting editor…
                  </span>
                }
                onChange={(v) => {
                  const next = v ?? "";
                  setContent(next);
                  if (target?.type === "file" && target.path) {
                    try {
                      markIdeTabModified?.(target.path, next !== original);
                    } catch (_) {}
                    try {
                      notifyChange?.(next);
                    } catch (_) {}
                  }
                }}
                onMount={(ed, monaco) => {
                  editorRef.current = ed;
                  if (monaco) monacoRef.current = monaco;
                  try {
                    notifyOpen?.(content || "");
                  } catch (_) {}
                  // Publish Monaco markers into IDE diagnostics
                  try {
                    const model = ed.getModel?.();
                    if (model && monaco?.editor?.onDidChangeMarkers) {
                      monaco.editor.onDidChangeMarkers(() => {
                        const marks = monaco.editor.getModelMarkers({ resource: model.uri }) || [];
                        const diags = marks.map((mk) => ({
                          path: tabPath || target?.path || "",
                          message: mk.message,
                          severity: mk.severity,
                          line: mk.startLineNumber,
                          column: mk.startColumn,
                        }));
                        mergeIdeDiagnostics?.(tabPath || target?.path || "unknown", diags);
                      });
                    }
                  } catch (_) {}
                }}
                options={{
                  fontSize: 13,
                  fontFamily: "'JetBrains Mono','Fira Code',monospace",
                  fontLigatures: true,
                  lineHeight: 1.6,
                  minimap: { enabled: ideSize.width > 720, scale: 0.8 },
                  scrollBeyondLastLine: false,
                  bracketPairColorization: { enabled: true },
                  smoothScrolling: true,
                  cursorBlinking: "smooth",
                  padding: { top: 10 },
                  tabSize: 2,
                  automaticLayout: true,
                }}
              />
            </div>
          )}

          {/* Bottom panel */}
          {bottomVisible && (
            <>
              {plan.bottomMode === "docked" && (
                <div
                  className="sp-ide-bottom-resizer"
                  onMouseDown={onBottomResize}
                  role="separator"
                  aria-orientation="horizontal"
                />
              )}
              <div
                className={`sp-ide-bottom sp-ide-bottom--${plan.bottomMode}`}
                style={
                  plan.bottomMode === "docked"
                    ? { height: plan.bottomHeight }
                    : undefined
                }
              >
                <div className="sp-ide-bottom-tabs">
                  {["terminal", "problems", "output"].map((id) => (
                    <button
                      key={id}
                      type="button"
                      className={
                        "sp-ide-tab" + (ideLayout.bottom === id ? " active" : "")
                      }
                      onClick={() => {
                        if (id === "terminal") openTerminalPanel();
                        else toggleIdeBottom(id);
                      }}
                    >
                      {id}
                    </button>
                  ))}
                  <span className="spacer" />
                  <button
                    type="button"
                    className="sp-iconbtn"
                    title="Close panel"
                    onClick={() => setIdeLayout({ bottomOpen: false })}
                  >
                    <X size={13} />
                  </button>
                </div>
                <div className="sp-ide-bottom-body">
                  {ideLayout.bottom === "problems" && (
                    <>
                    <div className="sp-ide-diag-list">
                      {flattenDiagnostics(ideWorkspace?.diagnostics || {}).length === 0 ? (
                        <div className="sp-ide-center-msg">No diagnostics</div>
                      ) : (
                        flattenDiagnostics(ideWorkspace?.diagnostics || {}).slice(0, 100).map((d, i) => (
                          <button
                            key={i}
                            type="button"
                            className="sp-ide-diag-item"
                            onClick={() => d.path && openEditor?.({ file: d.path })}
                          >
                            <span className="sp-ide-diag-path">{d.path}</span>
                            <span className="sp-ide-diag-msg">{d.message}</span>
                          </button>
                        ))
                      )}
                    </div>
                    <ProblemsPanel error={error || monacoError} statusText={statusText} />
                    </>
                  )}
                  {ideLayout.bottom === "output" && (
                    <OutputPanel statusText={statusText} />
                  )}
                  {ideLayout.bottom === "terminal" && (
                    <div className="sp-ide-panel-empty">
                      Terminal is available as a docked dimension.
                      <button
                        type="button"
                        className="sp-ide-linkbtn"
                        onClick={() => openTerminal?.({})}
                      >
                        Focus terminal
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="sp-ide-status">
        <span>{language}</span>
        <span>{loading ? "LOADING" : dirty ? "MODIFIED" : "SAVED"}</span>
        <span className="spacer" />
        <span>
          {plan.sidebarMode === "sheet" ? "side:sheet" : plan.sidebarMode}
          {" · "}
          {Math.round(ideSize.width)}×{Math.round(ideSize.height)}
        </span>
        <span>Ctrl+S to save</span>
      </div>
    </div>
  );
}
