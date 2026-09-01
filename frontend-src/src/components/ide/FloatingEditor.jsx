/**
 * DevOS FloatingEditor — Detachable editor window.
 * Drag-to-detach from the IDE panel; opens as a floating window.
 * Supports multi-monitor (window.open) and persists positions.
 */
import React, { Suspense, lazy, useState, useEffect, useRef, useCallback } from "react";
import { ExternalLink, Code, Pin } from "lucide-react";

const CodeEditor = lazy(() => import("../editor/CodeEditor"));

const STORAGE_KEY = "devos_floating_editor_pos";

function loadPosition() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { x: 200, y: 150, width: 700, height: 500 };
    return JSON.parse(raw);
  } catch {
    return { x: 200, y: 150, width: 700, height: 500 };
  }
}

export default function FloatingEditor({ initialFile, onPin }) {
  const [position, setPosition] = useState(loadPosition);
  const [isDragging, setIsDragging] = useState(false);
  const [isPinned, setIsPinned] = useState(false);
  const [file, setFile] = useState(initialFile);
  const dragStart = useRef({ x: 0, y: 0, posX: 0, posY: 0 });
  const editorRef = useRef(null);

  // Persist position (debounced)
  useEffect(() => {
    const t = setTimeout(() => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(position));
    }, 500);
    return () => clearTimeout(t);
  }, [position]);

  const handleMouseDown = useCallback((e) => {
    if (e.button !== 0) return;
    setIsDragging(true);
    dragStart.current = {
      x: e.clientX,
      y: e.clientY,
      posX: position.x,
      posY: position.y,
    };
  }, [position]);

  useEffect(() => {
    if (!isDragging) return;
    const handleMove = (e) => {
      const dx = e.clientX - dragStart.current.x;
      const dy = e.clientY - dragStart.current.y;
      setPosition((p) => ({
        ...p,
        x: dragStart.current.posX + dx,
        y: dragStart.current.posY + dy,
      }));
    };
    const handleUp = () => setIsDragging(false);
    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
  }, [isDragging]);

  const handleDetach = () => {
    // Open in a real browser window (multi-monitor support)
    const features = `width=${position.width},height=${position.height},left=${window.screenX + position.x},top=${window.screenY + position.y},menubar=no,toolbar=no,location=no`;
    window.open(window.location.href + "#editor-detached", "devos-editor", features);
  };

  const handlePin = () => {
    setIsPinned((p) => !p);
    if (onPin) onPin();
  };

  return (
    <div
      ref={editorRef}
      className={"devos-floating-editor" + (isDragging ? " dragging" : "") + (isPinned ? " pinned" : "")}
      style={{
        left: position.x,
        top: position.y,
        width: position.width,
        height: position.height,
        zIndex: isPinned ? 110 : 100,
      }}
    >
      {/* Drag handle */}
      <div
        className="devos-floating-editor-header"
        onMouseDown={handleMouseDown}
        style={{ cursor: isDragging ? "grabbing" : "grab" }}
      >
        <div className="devos-floating-editor-title">
          <Code size={13} />
          <span>{file ? file.name : "Editor"}</span>
        </div>
        <div className="devos-floating-editor-actions">
          <button onClick={handlePin} className="devos-floating-editor-btn" title="Pin (always on top)" aria-label="Pin editor">
            <Pin size={12} className={isPinned ? "active" : ""} />
          </button>
          <button onClick={handleDetach} className="devos-floating-editor-btn" title="Detach to window" aria-label="Detach editor to window">
            <ExternalLink size={12} />
          </button>
        </div>
      </div>

      {/* Editor body */}
      <div className="devos-floating-editor-body">
        <Suspense fallback={<div className="text-xs text-slate-400 p-4">Loading editor...</div>}>
          <CodeEditor file={file} onChange={setFile} />
        </Suspense>
      </div>
    </div>
  );
}
