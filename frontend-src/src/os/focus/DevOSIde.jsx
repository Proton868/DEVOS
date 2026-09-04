/**
 * DevOS IDE — the ephemeral editor focus surface.
 * Real file loading via /api/files/{project}/read or /api/scripts/{id},
 * real saving via writeFile / updateFlowScript. Monaco underneath.
 *
 * Monaco is loaded from the bundled package (see monacoSetup.js) so CSP
 * script-src 'self' does not leave the editor stuck on "Loading…".
 */
import React, { useEffect, useState, useCallback, useRef } from "react";
import Editor from "@monaco-editor/react";
import { ChevronLeft, Save, X, FileCode2, RefreshCw } from "lucide-react";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api, getLanguageFromPath } from "../../services/api";
import { ensureMonaco } from "../../monacoSetup";

ensureMonaco();

const LOAD_TIMEOUT_MS = 12000;

export default function DevOSIde({ onClose }) {
  const { editor, closeEditor } = useOsStore();
  const setStatus = useStore((s) => s.setStatus);
  const [content, setContent] = useState(null);
  const [original, setOriginal] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [monacoReady, setMonacoReady] = useState(false);
  const [monacoError, setMonacoError] = useState(null);
  const [monacoAttempt, setMonacoAttempt] = useState(0);
  const editorRef = useRef(null);
  const loadGen = useRef(0);

  const target = editor.file
    ? { type: "file", path: editor.file }
    : editor.scriptId != null
    ? { type: "script", id: editor.scriptId }
    : null;

  const language =
    editor.language ||
    (editor.file ? getLanguageFromPath(editor.file) : "python");

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

  // Bound Monaco init — never leave "Starting editor…" forever
  const MONACO_TIMEOUT_MS = 15000;
  useEffect(() => {
    if (loading || error || monacoReady || monacoError) return;
    const t = setTimeout(() => {
      setMonacoError(
        "Editor scripts did not become ready in time. Check network, CSP, or /static/monaco/vs assets."
      );
    }, MONACO_TIMEOUT_MS);
    return () => clearTimeout(t);
  }, [loading, error, monacoReady, monacoError, monacoAttempt]);


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
      setStatus("Saved ✓");
      setTimeout(() => useStore.getState().setStatus("Ready"), 1500);
    } catch (e) {
      setStatus("Save failed: " + e.message);
    } finally {
      setSaving(false);
    }
  }, [target, content, setStatus]);

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

  if (!target) return null;
  const title = target.type === "file" ? target.path : `script #${target.id}`;

  return (
    <div className="sp-surface sp-surface--seamless">
      <div className="sp-surface-head">
        <button className="sp-iconbtn" title="Back to canvas" onClick={() => { closeEditor(); onClose && onClose(); }}>
          <ChevronLeft size={15} />
        </button>
        <span>DEVOS IDE</span>
        <span className="sub">· {title}{dirty ? " ●" : ""}</span>
        <span className="spacer" />
        {error && (
          <button className="sp-iconbtn" title="Retry load" onClick={loadContent}>
            <RefreshCw size={14} />
          </button>
        )}
        <button className="sp-iconbtn" title={dirty ? "Save (unsaved changes)" : "Save"} disabled={!dirty || saving} onClick={save}>
          <Save size={15} />
        </button>
        <button className="sp-iconbtn" title="Close editor" onClick={() => { closeEditor(); onClose && onClose(); }}>
          <X size={15} />
        </button>
      </div>
      {loading ? (
        <div className="sp-insp-body" style={{ alignItems: "center", justifyContent: "center" }}>
          <FileCode2 size={22} style={{ color: "var(--sp-text-2)" }} />
          <span style={{ color: "var(--sp-text-2)" }}>Loading {title}…</span>
        </div>
      ) : error ? (
        <div className="sp-insp-body">
          <div className="sp-logline lg-error">Failed to load {title}: {error}</div>
          <button className="sp-chip" style={{ alignSelf: "flex-start" }} onClick={loadContent}>
            Retry
          </button>
        </div>
      ) : monacoError ? (
        <div className="sp-insp-body">
          <div className="sp-logline lg-error">Editor failed to initialize: {monacoError}</div>
          <button
            className="sp-chip"
            style={{ alignSelf: "flex-start" }}
            onClick={() => {
              setMonacoError(null);
              setMonacoReady(false);
              setMonacoAttempt((n) => n + 1);
            }}
          >
            Retry
          </button>
        </div>
      ) : (
        <div className="sp-ide-body">
          {!monacoReady && (
            <div className="sp-ide-monaco-loading">Starting editor…</div>
          )}
          <Editor
            key={`${title}:${monacoAttempt}`}
            language={language}
            value={content || ""}
            theme="vs-dark"
            loading={<span style={{ color: "var(--sp-text-2)", fontSize: 12 }}>Starting editor…</span>}
            onChange={(v) => setContent(v ?? "")}
            onMount={(ed) => {
              editorRef.current = ed;
              setMonacoReady(true);
              setMonacoError(null);
            }}
            options={{
              fontSize: 13,
              fontFamily: "'JetBrains Mono','Fira Code',monospace",
              fontLigatures: true,
              lineHeight: 1.6,
              minimap: { enabled: window.innerWidth > 720, scale: 0.8 },
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
      <div className="sp-ide-status">
        <span>{language}</span>
        <span>{loading ? "LOADING" : dirty ? "MODIFIED" : "SAVED"}</span>
        <span className="spacer" />
        <span>Ctrl+S to save</span>
      </div>
    </div>
  );
}
