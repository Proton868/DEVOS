/**
 * DevOS IDE — the ephemeral editor focus surface.
 * Appears spatially when the user selects "Edit Script" / "Open Code".
 * Real file loading via /api/files/{project}/read or /api/scripts/{id},
 * real saving via writeFile / updateFlowScript. Monaco underneath.
 */
import React, { useEffect, useState, useCallback, useRef } from "react";
import Editor from "@monaco-editor/react";
import { ChevronLeft, Save, X, FileCode2 } from "lucide-react";
import useOsStore from "../store/osStore";
import useStore from "../../store/useStore";
import { api, getLanguageFromPath } from "../../services/api";

export default function DevOSIde({ onClose }) {
  const { editor, closeEditor } = useOsStore();
  const setStatus = useStore((s) => s.setStatus);
  const [content, setContent] = useState(null);
  const [original, setOriginal] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const editorRef = useRef(null);

  const target = editor.file
    ? { type: "file", path: editor.file }
    : editor.scriptId != null
    ? { type: "script", id: editor.scriptId }
    : null;

  const language =
    editor.language ||
    (editor.file ? getLanguageFromPath(editor.file) : "python");

  // Load real content
  useEffect(() => {
    if (!target) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        let code = "";
        let lang = language;
        if (target.type === "file") {
          const r = await api.readFile(target.path);
          code = r?.content ?? r?.data ?? (typeof r === "string" ? r : "");
          lang = getLanguageFromPath(target.path);
        } else {
          const s = await api.flowScript(target.id);
          code = s?.code ?? s?.content ?? "";
          if (s?.language) lang = s.language.toLowerCase() === "python" ? "python" : s.language.toLowerCase();
        }
        if (cancelled) return;
        setContent(code);
        setOriginal(code);
        useOsStore.setState((st) => ({ editor: { ...st.editor, language: lang } }));
      } catch (e) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [target?.type, target?.path, target?.id]);

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

  // Ctrl/Cmd+S
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
    <div className="sp-surface">
      <div className="sp-surface-head">
        <button className="sp-iconbtn" title="Back to canvas" onClick={() => { closeEditor(); onClose && onClose(); }}>
          <ChevronLeft size={15} />
        </button>
        <span>DEVOS IDE</span>
        <span className="sub">· {title}{dirty ? " ●" : ""}</span>
        <span className="spacer" />
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
        </div>
      ) : (
        <div className="sp-ide-body">
          <Editor
            key={title}
            language={language}
            value={content || ""}
            theme="vs-dark"
            onChange={(v) => setContent(v ?? "")}
            onMount={(ed) => { editorRef.current = ed; }}
            options={{
              fontSize: 13,
              fontFamily: "'JetBrains Mono','Fira Code',monospace",
              fontLigatures: true,
              lineHeight: 1.6,
              minimap: { enabled: true, scale: 0.8 },
              scrollBeyondLastLine: false,
              bracketPairColorization: { enabled: true },
              smoothScrolling: true,
              cursorBlinking: "smooth",
              padding: { top: 10 },
              tabSize: 4,
            }}
          />
        </div>
      )}
      <div className="sp-ide-status">
        <span>{language}</span>
        <span>{dirty ? "MODIFIED" : "SAVED"}</span>
        <span className="spacer" />
        <span>Ctrl+S to save</span>
      </div>
    </div>
  );
}